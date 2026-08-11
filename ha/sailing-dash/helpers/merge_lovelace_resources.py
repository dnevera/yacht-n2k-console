#!/usr/bin/env python3
"""The ONE implementation of the .storage/lovelace_resources merge.

Both deploy paths use it, so Stage and Prod can never drift apart:
  - deploy.sh          runs it as a CLI (registry JSON in, merged JSON out)
  - stage_provisioner  imports merge_registry() directly

Merge rules:
  - a resource is matched by URL *path* (the ?v=... cache-buster may change);
  - existing entries are updated in place, new ones get a fresh id;
  - nothing is ever removed (HACS-installed resources of the target survive);
  - on a target without HACS, /hacsfiles/<name> is normalised to /local/<name>
    and dropped entirely when we have no bundle for it, instead of failing the
    whole deploy over an optional card.

Usage:
    merge_lovelace_resources.py <resources.yaml> <current.json> <out.json> \
                                <stage|prod> <cards_dir> <deps_cards_dir>
"""

import os
import sys
import json
import uuid
from urllib.parse import urlsplit


def load_wanted(resources_yaml):
    import yaml
    with open(resources_yaml, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    wanted = data.get("resources", []) or []
    return [r for r in wanted if urlsplit(r["url"]).path.startswith(("/local/", "/hacsfiles/"))]


def normalize(wanted, target_env, cards_dir, deps_cards_dir):
    """Rewrites /hacsfiles/ URLs to /local/ on a target without HACS."""
    def has_bundle(filename):
        return (os.path.isfile(os.path.join(cards_dir, filename))
                or os.path.isfile(os.path.join(deps_cards_dir, filename)))

    result = []
    for entry in wanted:
        url = entry["url"]
        rtype = entry.get("type", "module")
        if target_env == "stage" and url.startswith("/hacsfiles/"):
            filename = url.rsplit("/", 1)[-1]
            if not has_bundle(filename):
                print(f"SKIP   {url} (no build/deps bundle for {filename}, not required on stage)")
                continue
            result.append({"url": f"/local/{filename}", "type": rtype})
        else:
            result.append({"url": url, "type": rtype})
    return result


def merge_registry(registry, wanted, target_env="stage", cards_dir="", deps_cards_dir=""):
    """Merges `wanted` into an HA lovelace_resources registry dict.

    Returns (registry, files_to_upload)."""
    if not isinstance(registry, dict) or "data" not in registry:
        registry = {"version": 1, "minor_version": 1,
                    "key": "lovelace_resources", "data": {"items": []}}

    wanted = normalize(wanted, target_env, cards_dir, deps_cards_dir)
    items = registry.setdefault("data", {}).setdefault("items", [])
    existing_by_path = {urlsplit(it.get("url", "")).path: it for it in items}

    to_upload = []
    for entry in wanted:
        path = urlsplit(entry["url"]).path
        existing = existing_by_path.get(path)
        if existing is None:
            items.append({"id": uuid.uuid4().hex[:24],
                          "url": entry["url"],
                          "type": entry.get("type", "module")})
            print(f"ADD    {entry['url']}")
        elif existing.get("url") != entry["url"]:
            existing["url"] = entry["url"]
            existing["type"] = entry.get("type", "module")
            print(f"UPDATE {path} -> {entry['url']}")
        else:
            print(f"OK     {entry['url']} (already registered)")
        if path.startswith("/local/"):
            to_upload.append(path.rsplit("/", 1)[-1])

    registry["data"]["items"] = items
    return registry, to_upload


def main(argv):
    if len(argv) != 7:
        print(__doc__, file=sys.stderr)
        return 2
    resources_yaml, current_json, out_json, target_env, cards_dir, deps_cards_dir = argv[1:7]

    try:
        with open(current_json, "r", encoding="utf-8") as fh:
            registry = json.load(fh)
    except Exception:
        registry = {}

    registry, to_upload = merge_registry(
        registry, load_wanted(resources_yaml), target_env, cards_dir, deps_cards_dir)

    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2)
    with open(out_json + ".files", "w", encoding="utf-8") as fh:
        fh.write("\n".join(to_upload) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
