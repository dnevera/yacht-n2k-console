#!/usr/bin/env python3
"""Download every external artifact declared in deps.yaml into build/deps/.

This is the ONLY downloader in the project. There is no vendored copy of any
third-party bundle or integration in the repo any more, and no `.cache/`:
`build/deps/` is an ordinary build artifact directory (gitignored, wiped
together with `build/`).

Layout produced:

    build/deps/cards/<asset>.js                       frontend card bundles
    build/deps/hacs/custom_components/hacs/           HACS integration
    build/deps/nmea2000/custom_components/nmea2000/   our ha-nmea2000 fork

Usage:
    python3 fetch_deps.py                 # fetch everything that's missing
    python3 fetch_deps.py --force         # re-download even if present
    python3 fetch_deps.py --only cards    # cards | integrations
    python3 fetch_deps.py --update-hashes # record sha256 of what was fetched
"""

import os
import sys
import ssl
import time
import shutil
import hashlib
import zipfile
import argparse
import tempfile
import urllib.error
import urllib.request

try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# This script lives in ha/sailing-dash/helpers/; deps.yaml and build/ belong to
# the subproject root one level up.
HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(HELPERS_DIR)
DEPS_FILE = os.path.join(SCRIPT_DIR, "deps.yaml")
DEPS_DIR = os.path.join(SCRIPT_DIR, "build", "deps")
CARDS_DIR = os.path.join(DEPS_DIR, "cards")

RETRIES = 3
TIMEOUT = 60
RETRY_DELAY = 2


def log(level, msg):
    prefix = {
        "INFO": "\033[94m[INFO]\033[0m",
        "FETCH": "\033[92m[FETCH]\033[0m",
        "WARN": "\033[93m[WARN]\033[0m",
        "ERROR": "\033[91m[ERROR]\033[0m",
    }.get(level, f"[{level}]")
    print(f"{prefix} {msg}")


def load_deps(path=DEPS_FILE):
    if not os.path.isfile(path):
        raise SystemExit(f"[ERROR] Dependency manifest not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest):
    """Downloads url to dest with retries. Raises RuntimeError on failure."""
    last_err = None
    ctx = ssl.create_default_context()
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sailing-dash-fetch-deps"})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp, \
                    open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            return
        except Exception as exc:  # noqa: BLE001 - network errors of any kind
            last_err = exc
            if attempt < RETRIES:
                log("WARN", f"{url} failed ({exc}); retry {attempt + 1}/{RETRIES} ...")
                time.sleep(RETRY_DELAY * attempt)
    raise RuntimeError(f"could not download {url}: {last_err}")


def resolve_url(spec):
    """Builds the download URL for one manifest entry."""
    src = spec.get("source")
    repo = spec.get("repo")
    ref = spec.get("ref")
    if src == "github_release_asset":
        if ref in (None, "latest"):
            return f"https://github.com/{repo}/releases/latest/download/{spec['asset']}"
        return f"https://github.com/{repo}/releases/download/{ref}/{spec['asset']}"
    if src == "github_raw":
        return f"https://raw.githubusercontent.com/{repo}/{ref}/{spec['path']}"
    if src == "github_tag_archive":
        return f"https://github.com/{repo}/archive/refs/tags/{ref}.zip"
    raise RuntimeError(f"unsupported source '{src}' for {spec.get('name')}")


def verify(spec, path):
    expected = spec.get("sha256")
    actual = sha256_of(path)
    if expected and expected != actual:
        raise RuntimeError(
            f"sha256 mismatch for {spec['name']}: expected {expected}, got {actual}"
        )
    return actual


def fetch_card(spec, force, hashes):
    os.makedirs(CARDS_DIR, exist_ok=True)
    dest = os.path.join(CARDS_DIR, spec["asset"])
    if os.path.isfile(dest) and not force:
        if spec.get("sha256") and sha256_of(dest) != spec["sha256"]:
            log("WARN", f"{spec['asset']} present but sha256 differs — re-downloading")
        else:
            log("INFO", f"{spec['asset']} already built ({spec['ref']})")
            hashes[("cards", spec["name"])] = sha256_of(dest)
            return
    url = resolve_url(spec)
    log("FETCH", f"{spec['name']} {spec['ref']} <- {url}")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        download(url, tmp_path)
        digest = verify(spec, tmp_path)
        shutil.move(tmp_path, dest)
        hashes[("cards", spec["name"])] = digest
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _extract_component(zip_path, component_path, dest_dir):
    """Extracts <component_path> out of the archive into dest_dir (replacing it)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmpdir)
        # GitHub tag archives wrap everything in a single top-level directory;
        # release .zip files (HACS) do not.
        candidates = [os.path.join(tmpdir, component_path)]
        for entry in os.listdir(tmpdir):
            candidates.append(os.path.join(tmpdir, entry, component_path))
        # HACS ships the component contents at the archive root.
        candidates.append(tmpdir)
        for cand in candidates:
            if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "manifest.json")):
                if os.path.isdir(dest_dir):
                    shutil.rmtree(dest_dir)
                os.makedirs(os.path.dirname(dest_dir), exist_ok=True)
                shutil.copytree(cand, dest_dir)
                return True
        return False


def fetch_integration(spec, force, hashes):
    name = spec["name"]
    base = os.path.join(DEPS_DIR, name)
    dest = os.path.join(base, spec.get("component_path", f"custom_components/{name}"))
    if os.path.isdir(dest) and os.path.isfile(os.path.join(dest, "manifest.json")) and not force:
        log("INFO", f"integration {name} already built ({spec['ref']})")
        return
    url = resolve_url(spec)
    log("FETCH", f"integration {name} {spec['ref']} <- {url}")
    os.makedirs(base, exist_ok=True)
    zip_path = os.path.join(base, f"{name}.zip")
    download(url, zip_path)
    digest = verify(spec, zip_path)
    if spec.get("ref") not in (None, "latest"):
        hashes[("integrations", name)] = digest
    if not _extract_component(zip_path, spec.get("component_path", ""), dest):
        raise RuntimeError(
            f"{name}: could not locate '{spec.get('component_path')}' with a manifest.json "
            f"inside {url}"
        )
    os.unlink(zip_path)


def update_hashes(deps, hashes, path=DEPS_FILE):
    """Rewrites the sha256 values in deps.yaml in place, preserving comments."""
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    current = None
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- name:"):
            current = stripped.split(":", 1)[1].strip()
        if stripped.startswith("sha256:") and current:
            digest = next((v for (_sec, n), v in hashes.items() if n == current), None)
            if digest:
                indent = line[: len(line) - len(line.lstrip())]
                comment = line.split("#", 1)[1].rstrip() if "#" in line else ""
                suffix = f"  #{comment}" if comment else ""
                out.append(f'{indent}sha256: "{digest}"{suffix}\n')
                continue
        out.append(line)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    log("INFO", f"recorded sha256 for {len(hashes)} artifact(s) in {os.path.basename(path)}")


def main():
    ap = argparse.ArgumentParser(description="Download deps.yaml artifacts into build/deps/")
    ap.add_argument("--only", choices=["cards", "integrations"], help="fetch one section only")
    ap.add_argument("--force", action="store_true", help="re-download even if already present")
    ap.add_argument("--update-hashes", action="store_true",
                    help="write the sha256 of every fetched artifact back into deps.yaml")
    args = ap.parse_args()

    deps = load_deps()
    hashes = {}
    failures = []

    if args.only in (None, "cards"):
        for spec in deps.get("cards", []) or []:
            try:
                fetch_card(spec, args.force, hashes)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"card {spec.get('name')}: {exc}")

    if args.only in (None, "integrations"):
        for spec in deps.get("integrations", []) or []:
            try:
                fetch_integration(spec, args.force, hashes)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"integration {spec.get('name')}: {exc}")

    if args.update_hashes and hashes:
        update_hashes(deps, hashes)

    if failures:
        log("ERROR", "the following artifacts could not be fetched — deploy cannot continue:")
        for item in failures:
            print(f"  - {item}")
        print("\nFix your network/GitHub access (or the pinned tag in deps.yaml) and re-run\n"
              "  python3 helpers/fetch_deps.py\n"
              "Nothing is taken from a stale local copy on purpose: a deploy must be\n"
              "reproducible from the pinned tags alone.")
        return 1

    log("INFO", f"all dependencies present in {os.path.relpath(DEPS_DIR, SCRIPT_DIR)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
