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
    python3 provision_helpers.py <.storage/input_boolean path> [--write] \
        [--entity-registry <.storage/core.entity_registry path>]

    Without --write: report only; exit 0 if nothing needs to change, 2 if it
    does (so deploy.sh can branch without parsing stdout).
    With --write: patches (or creates) the file(s) in place, idempotently.

A missing file is fine: HA creates `.storage/input_boolean` only once a helper
exists, so this script writes a fresh, minimally-valid document in that case.

IMPORTANT — the entity_id comes from `name`, not from `id`.
Home Assistant slugifies the helper's DISPLAY NAME to build its entity_id, so
the name MUST be "AIS table expanded" to yield
`input_boolean.ais_table_expanded`. An earlier revision shipped the name "AIS
Targets", which produced `input_boolean.ais_targets` instead — the dashboard's
two `conditional` cards then never matched and the whole target-list overlay
stayed invisible. Renaming alone cannot fix an already-registered helper (the
entity_id is pinned in `core.entity_registry`), hence `--entity-registry`: it
drops the mis-slugged registry row so HA re-registers it correctly on restart.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HELPER_ID = "ais_table_expanded"
HELPER_ENTITY_ID = f"input_boolean.{HELPER_ID}"
HELPER_CONFIG = {
    "id": HELPER_ID,
    # Must slugify to HELPER_ID — see the module docstring.
    "name": "AIS table expanded",
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
            if item.get("name") == HELPER_CONFIG["name"]:
                messages.append(f"{HELPER_ENTITY_ID} already exists — no change.")
                return False, messages
            item["name"] = HELPER_CONFIG["name"]
            messages.append(
                f"Renamed the overlay toggle to '{HELPER_CONFIG['name']}' so it "
                f"slugifies to {HELPER_ENTITY_ID}."
            )
            return True, messages

    items.append(dict(HELPER_CONFIG))
    messages.append(f"Added {HELPER_ENTITY_ID} (AIS table overlay toggle).")
    return True, messages


def compute_registry_patch(doc: dict) -> tuple[bool, list[str]]:
    """Drop a mis-slugged registry row for our helper, so HA re-registers it
    under HELPER_ENTITY_ID on the next start. Mutates `doc` in place."""
    messages: list[str] = []
    entities = doc.get("data", {}).get("entities")
    if not isinstance(entities, list):
        return False, ["entity registry has no 'entities' list — skipped."]

    stale = [
        e
        for e in entities
        if isinstance(e, dict)
        and e.get("platform") == "input_boolean"
        and e.get("unique_id") == HELPER_ID
        and e.get("entity_id") != HELPER_ENTITY_ID
    ]
    if not stale:
        messages.append(f"entity registry: no stale row for {HELPER_ID}.")
        return False, messages

    for entry in stale:
        entities.remove(entry)
        messages.append(
            f"entity registry: removed mis-slugged row {entry.get('entity_id')} "
            f"— HA will re-register it as {HELPER_ENTITY_ID}."
        )
    return True, messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_boolean_path", help="path to .storage/input_boolean")
    parser.add_argument(
        "--write",
        action="store_true",
        help="patch the file in place instead of only reporting",
    )
    parser.add_argument(
        "--entity-registry",
        help="path to .storage/core.entity_registry, to drop a mis-slugged row",
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
    if not os.path.exists(args.input_boolean_path):
        changed = True

    reg_doc = None
    reg_changed = False
    if args.entity_registry and os.path.exists(args.entity_registry):
        with open(args.entity_registry, "r", encoding="utf-8") as f:
            reg_doc = json.load(f)
        reg_changed, reg_messages = compute_registry_patch(reg_doc)
        for message in reg_messages:
            print(message)

    if not changed and not reg_changed:
        return 0

    if args.write:
        if changed:
            with open(args.input_boolean_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"Wrote {args.input_boolean_path}")
        if reg_changed and reg_doc is not None:
            with open(args.entity_registry, "w", encoding="utf-8") as f:
                json.dump(reg_doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"Wrote {args.entity_registry}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
