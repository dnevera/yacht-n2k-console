#!/usr/bin/env python3
"""
patch_pgn_include.py — idempotently add the AIS PGN set to the `pgn_include`
allow-list of a live `nmea2000` config entry stored in
`.storage/core.config_entries`.

Analogous in spirit to how ha/sailing-dash/helpers/merge_lovelace_resources.py
and stage_provisioner.py do idempotent JSON merges: read the live document,
compute the merged result, write it back ONLY if something actually changed,
and never touch anything else in the file.

`pgn_include` is documented (see the project's nmea2000-setup skill and
ha/sailing-dash's map_nmea_sensors.py comments) as a SILENT allow-list: a PGN
missing from it produces no error, it just never gets an entity. That is
exactly the failure mode this script exists to prevent for the AIS PGNs.

USAGE
    python3 patch_pgn_include.py <core.config_entries path> [--write]

    Without --write: prints what WOULD change and exits 0 if nothing needs to
    change, 2 if a change is needed (so a caller shell script can decide).
    With --write: patches the file in place (still idempotent - a no-op run
    on an already-patched file exits 0 without touching the file's mtime).

This script only edits the JSON on disk; ha/ais/deploy.sh is responsible for
fetching core.config_entries out of the container, running this script, and
copying the result back in (see ha_cp_to_container_if_changed()).
"""
from __future__ import annotations

import argparse
import json
import sys

# PGNs decodable by this project's nmea2000 fork (see .agents/skills/
# nmea2000-setup/SKILL.md and nmea2000/pgns.py) needed for full AIS coverage:
# Class A/B position reports, Class B extended position, Aid to Navigation,
# UTC/date report, Class A static & voyage data, Class B static data (msg 24
# parts A/B).
AIS_PGNS = [129038, 129039, 129040, 129041, 129793, 129794, 129809, 129810]

NMEA2000_DOMAIN = "nmea2000"


def _iter_nmea2000_entries(config_entries_doc: dict):
    entries = config_entries_doc.get("data", {}).get("entries", [])
    for entry in entries:
        if isinstance(entry, dict) and entry.get("domain") == NMEA2000_DOMAIN:
            yield entry


def compute_patch(config_entries_doc: dict) -> tuple[bool, list[str]]:
    """Return `(changed, messages)`.

    `changed` is True when at least one nmea2000 entry's `pgn_include` was
    missing one or more AIS PGNs and got patched IN PLACE (mutates
    `config_entries_doc`). `messages` is a human-readable log, one line per
    entry inspected.
    """
    changed = False
    messages: list[str] = []

    entries = list(_iter_nmea2000_entries(config_entries_doc))
    if not entries:
        messages.append(
            "No nmea2000 config entry found in core.config_entries — nothing "
            "to patch (install/configure the nmea2000 integration first)."
        )
        return changed, messages

    for entry in entries:
        options = entry.setdefault("options", {}) or {}
        entry["options"] = options
        data = entry.setdefault("data", {}) or {}

        # Ensure exclude_AIS is False so AIS messages are not dropped
        if data.get("exclude_AIS") is not False:
            data["exclude_AIS"] = False
            changed = True
            messages.append(f"nmea2000 entry {entry.get('entry_id', '<unknown>')}: set exclude_AIS = False.")
        if options.get("exclude_AIS") is not False and "exclude_AIS" in options:
            options["exclude_AIS"] = False
            changed = True

        current = options.get("pgn_include")
        target_dict = options
        if current is None and data.get("pgn_include") is not None:
            current = data.get("pgn_include")
            target_dict = data

        is_string_type = False
        if isinstance(current, str):
            current_list = [int(p.strip()) for p in current.split(",") if p.strip().isdigit()]
            is_string_type = True
        elif isinstance(current, list):
            current_list = list(current)
        else:
            current_list = []

        missing = [pgn for pgn in AIS_PGNS if pgn not in current_list]

        entry_id = entry.get("entry_id", "<unknown>")
        if not missing:
            messages.append(
                f"nmea2000 entry {entry_id}: pgn_include already covers all "
                f"AIS PGNs ({AIS_PGNS}) — no change needed."
            )
            continue

        new_list = current_list + missing
        if is_string_type:
            target_dict["pgn_include"] = ",".join(str(p) for p in new_list)
        else:
            target_dict["pgn_include"] = new_list

        changed = True
        messages.append(
            f"nmea2000 entry {entry_id}: added missing AIS PGNs {missing} to "
            f"pgn_include (was {current_list})."
        )

    return changed, messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_entries_path", help="path to core.config_entries")
    parser.add_argument(
        "--write",
        action="store_true",
        help="patch the file in place instead of only reporting",
    )
    args = parser.parse_args()

    with open(args.config_entries_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    changed, messages = compute_patch(doc)
    for message in messages:
        print(message)

    if not changed:
        return 0

    if args.write:
        with open(args.config_entries_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"Wrote patched {args.config_entries_path}")
        return 0

    # --write not given: report-only mode signals "a change IS needed" with
    # exit code 2, so deploy.sh can branch on it without parsing stdout.
    return 2


if __name__ == "__main__":
    sys.exit(main())
