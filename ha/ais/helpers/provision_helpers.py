#!/usr/bin/env python3
"""
provision_helpers.py — idempotently ensure the Lovelace helpers the AIS
dashboard needs exist in a live Home Assistant instance.

Right now that is a single toggle, `input_boolean.ais_table_expanded`, which
drives the target-list overlay on the AIS map: OFF renders the compact
side-bar (vessel name + distance), ON expands it into the full table (see
src/yaml/dashboard/sections/01_ais_map.yaml). UI-created helpers live in
`.storage/input_boolean`, so this script merges the entry into that document —
same read / compute / write-only-if-changed convention as
patch_pgn_include.py and ha/sailing-dash/helpers/merge_lovelace_resources.py.

USAGE
    python3 provision_helpers.py <.storage/input_boolean path> [--write]

    Without --write: report only; exit 0 if nothing needs to change, 2 if it
    does (so deploy.sh can branch without parsing stdout).
    With --write: patches (or creates) the file in place, idempotently.

A missing file is fine: HA creates `.storage/input_boolean` only once a helper
exists, so this script writes a fresh, minimally-valid document in that case.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HELPER_ID = "ais_table_expanded"
HELPER_CONFIG = {
    "id": HELPER_ID,
    "name": "AIS Targets",
    "icon": "mdi:table-eye",
}
EMPTY_DOC = {
    "version": 1,
    "minor_version": 1,
    "key": "input_boolean",
    "data": {"items": []},
}


def compute_patch(doc: dict) -> tuple[bool, list[str]]:
    """Return `(changed, messages)`, mutating `doc` in place."""
    messages: list[str] = []
    data = doc.setdefault("data", {})
    items = data.setdefault("items", [])

    for item in items:
        if isinstance(item, dict) and item.get("id") == HELPER_ID:
            messages.append(
                f"input_boolean.{HELPER_ID} already exists — no change."
            )
            return False, messages

    items.append(dict(HELPER_CONFIG))
    messages.append(f"Added input_boolean.{HELPER_ID} (AIS table overlay toggle).")
    return True, messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_boolean_path", help="path to .storage/input_boolean")
    parser.add_argument(
        "--write",
        action="store_true",
        help="patch the file in place instead of only reporting",
    )
    args = parser.parse_args()

    if os.path.exists(args.input_boolean_path):
        with open(args.input_boolean_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = json.loads(json.dumps(EMPTY_DOC))
        print(
            f"{args.input_boolean_path} does not exist yet — a fresh "
            "input_boolean store will be created."
        )

    changed, messages = compute_patch(doc)
    for message in messages:
        print(message)

    if not changed and os.path.exists(args.input_boolean_path):
        return 0

    if args.write:
        with open(args.input_boolean_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"Wrote {args.input_boolean_path}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
