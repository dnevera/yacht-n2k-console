#!/usr/bin/env python3
"""Minimal Spec-Driven Development helper CLI.

Manages markdown specs stored in ``specs/active`` and ``specs/completed``.
Stdlib only - no extra dependencies are allowed for this tool.

Commands:
    create   -- instantiate a template into specs/active with the next free number
    list     -- list specs with their id, status and title
    validate -- ensure every required H2 section is present (exit code 1 otherwise)
    archive  -- move a spec from specs/active to specs/completed
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"

REQUIRED_SECTIONS = [
    "Metadata",
    "Context",
    "Requirements",
    "Architecture & Technical Design",
    "Interfaces / Contracts",
    "Implementation Plan",
    "Verification",
]

TEMPLATES = {
    "feature": "feature_template.md",
    "bugfix": "bugfix_template.md",
    "n2k-device": "n2k_device_template.md",
}

SPEC_NAME_RE = re.compile(r"^(\d{3})-(.+)\.md$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^-\s*status:\s*(\S+)\s*$", re.MULTILINE)


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9\u0400-\u04ff]+", "-", title.strip().lower())
    return slug.strip("-") or "spec"


def _active_dir(root: Path) -> Path:
    return root / "active"


def _completed_dir(root: Path) -> Path:
    return root / "completed"


def _iter_specs(directory: Path):
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if SPEC_NAME_RE.match(p.name))


def _next_number(root: Path) -> int:
    used = []
    for directory in (_active_dir(root), _completed_dir(root)):
        for path in _iter_specs(directory):
            match = SPEC_NAME_RE.match(path.name)
            if match:
                used.append(int(match.group(1)))
    return (max(used) + 1) if used else 1


def _sections(text: str) -> list[str]:
    return [name.strip() for name in H2_RE.findall(text)]


def _missing_sections(text: str) -> list[str]:
    present = {name.lower() for name in _sections(text)}
    return [name for name in REQUIRED_SECTIONS if name.lower() not in present]


def _title(text: str, fallback: str) -> str:
    match = H1_RE.search(text)
    return match.group(1).strip() if match else fallback


def _status(text: str) -> str:
    match = STATUS_RE.search(text)
    return match.group(1) if match else "unknown"


def cmd_create(args: argparse.Namespace) -> int:
    root = args.specs_dir
    template_path = root / "templates" / TEMPLATES[args.type]
    if not template_path.is_file():
        print(f"error: template not found: {template_path}", file=sys.stderr)
        return 1

    active = _active_dir(root)
    active.mkdir(parents=True, exist_ok=True)

    number = _next_number(root)
    target = active / f"{number:03d}-{_slugify(args.title)}.md"
    if target.exists():
        print(f"error: spec already exists: {target}", file=sys.stderr)
        return 1

    text = template_path.read_text(encoding="utf-8")
    text = H1_RE.sub(f"# {args.title}", text, count=1)
    text = text.replace("- id: NNN", f"- id: {number:03d}", 1)
    text = text.replace("- date: YYYY-MM-DD", f"- date: {_dt.date.today().isoformat()}", 1)
    target.write_text(text, encoding="utf-8")

    print(target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = args.specs_dir
    buckets = []
    if args.status in (None, "active"):
        buckets.append(("active", _active_dir(root)))
    if args.status in (None, "completed"):
        buckets.append(("completed", _completed_dir(root)))

    total = 0
    for label, directory in buckets:
        print(f"[{label}]")
        specs = _iter_specs(directory)
        if not specs:
            print("  (empty)")
            continue
        for path in specs:
            text = path.read_text(encoding="utf-8")
            number = SPEC_NAME_RE.match(path.name).group(1)
            print(f"  {number}  {_status(text):<12} {_title(text, path.stem)}  ({path.name})")
            total += 1
    print(f"total: {total}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = args.specs_dir
    if args.paths:
        paths = [Path(p) for p in args.paths]
    else:
        paths = _iter_specs(_active_dir(root)) + _iter_specs(_completed_dir(root))

    if not paths:
        print("no specs to validate")
        return 0

    failed = 0
    for path in paths:
        if not path.is_file():
            print(f"FAIL {path}: file not found")
            failed += 1
            continue
        missing = _missing_sections(path.read_text(encoding="utf-8"))
        if missing:
            failed += 1
            print(f"FAIL {path}: missing sections: {', '.join(missing)}")
        else:
            print(f"OK   {path}")

    if failed:
        print(f"{failed} spec(s) failed validation", file=sys.stderr)
        return 1
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    root = args.specs_dir
    source = Path(args.path)
    if not source.is_file():
        print(f"error: spec not found: {source}", file=sys.stderr)
        return 1

    completed = _completed_dir(root)
    completed.mkdir(parents=True, exist_ok=True)
    target = completed / source.name
    if target.exists():
        print(f"error: already archived: {target}", file=sys.stderr)
        return 1

    text = source.read_text(encoding="utf-8")
    text = STATUS_RE.sub("- status: completed", text, count=1)
    target.write_text(text, encoding="utf-8")
    source.unlink()
    print(f"archived: {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spec.py", description="Spec-Driven Development helper")
    parser.add_argument(
        "--specs-dir",
        type=Path,
        default=SPECS_DIR,
        help="root of the specs directory (default: <repo>/specs)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="create a new spec from a template")
    create.add_argument("--type", choices=sorted(TEMPLATES), default="feature")
    create.add_argument("--title", required=True)
    create.set_defaults(func=cmd_create)

    listing = sub.add_parser("list", help="list specs")
    listing.add_argument("--status", choices=["active", "completed"], default=None)
    listing.set_defaults(func=cmd_list)

    validate = sub.add_parser("validate", help="validate required sections")
    validate.add_argument("paths", nargs="*")
    validate.set_defaults(func=cmd_validate)

    archive = sub.add_parser("archive", help="move a spec to specs/completed")
    archive.add_argument("path")
    archive.set_defaults(func=cmd_archive)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
