#!/usr/bin/env python3
"""
patch_pgn_include.py — idempotently KEEP THE AIS PGNs OUT of the `pgn_include`
allow-list of a live `nmea2000` config entry stored in
`.storage/core.config_entries`, and force `exclude_AIS = True`.

⚠️ INVERTED ON 2026-08-13 (re-architecture):
Earlier this script *added* the AIS PGNs to `pgn_include` so the `nmea2000` HA
integration would decode AIS into entities. That approach was abandoned: the
`ais_targets` custom integration now reads AIS straight from the gateway and
decodes it in RAM, so the `nmea2000` HA integration must be kept AWAY from AIS
— otherwise it recreates a throwaway device + sensors per passing MMSI and
floods HA's registry / recorder DB. This script therefore now REMOVES the AIS
PGNs from `pgn_include` (and sets `exclude_AIS = True`) so a previously-patched
instance stops decoding AIS the moment `ha/ais/deploy.sh --install` runs.

Analogous in spirit to ha/sailing-dash/helpers/merge_lovelace_resources.py /
stage_provisioner.py: read the live document, compute the result, write it back
ONLY if something actually changed, and never touch anything else.

USAGE
    python3 patch_pgn_include.py <core.config_entries path> [--write]

    Without --write: prints what WOULD change and exits 0 if nothing needs to
    change, 2 if a change is needed (so a caller shell script can decide).
    With --write: patches the file in place (still idempotent — a no-op run on
    an already-clean file exits 0 without touching the file's mtime).

This script only edits the JSON on disk; ha/ais/deploy.sh is responsible for
fetching core.config_entries out of the container, running this script, and
copying the result back in (see ha_cp_to_container_if_changed()).
"""
from __future__ import annotations

import argparse
import json
import sys

# The AIS PGNs the ais_targets custom integration decodes itself off the
# gateway — these must NOT be present in the nmea2000 HA integration's
# pgn_include allow-list (see the module docstring for the reasoning).
AIS_PGNS = [129038, 129039, 129040, 129041, 129793, 129794, 129809, 129810]

NMEA2000_DOMAIN = "nmea2000"


def _iter_nmea2000_entries(config_entries_doc: dict):
    entries = config_entries_doc.get("data", {}).get("entries", [])
    for entry in entries:
        if isinstance(entry, dict) and entry.get("domain") == NMEA2000_DOMAIN:
            yield entry


def compute_patch(config_entries_doc: dict) -> tuple[bool, list[str]]:
    """Return `(changed, messages)`.

    `changed` is True when at least one nmea2000 entry still had an AIS PGN in
    its `pgn_include` (removed here) or `exclude_AIS` not set to True (mutates
    `config_entries_doc`). `messages` is a human-readable log.
    """
    changed = False
    messages: list[str] = []

    entries = list(_iter_nmea2000_entries(config_entries_doc))
    if not entries:
        messages.append(
            "No nmea2000 config entry found in core.config_entries — nothing "
            "to clean (AIS is decoded by ais_targets, not this integration)."
        )
        return changed, messages

    ais_set = set(AIS_PGNS)

    for entry in entries:
        options = entry.setdefault("options", {}) or {}
        entry["options"] = options
        data = entry.setdefault("data", {}) or {}
        entry_id = entry.get("entry_id", "<unknown>")
        entry_changed = False

        # Force exclude_AIS = True wherever the key lives (data and/or options).
        for holder, label in ((data, "data"), (options, "options")):
            if "exclude_AIS" in holder and holder.get("exclude_AIS") is not True:
                holder["exclude_AIS"] = True
                entry_changed = True
                messages.append(
                    f"nmea2000 entry {entry_id}: set {label}.exclude_AIS = True."
                )
        # If exclude_AIS was absent entirely, add it to data (the canonical
        # place the integration reads it from) so AIS is dropped by default.
        if "exclude_AIS" not in data and "exclude_AIS" not in options:
            data["exclude_AIS"] = True
            entry_changed = True
            messages.append(
                f"nmea2000 entry {entry_id}: added data.exclude_AIS = True."
            )

        # Strip AIS PGNs out of pgn_include wherever it is stored.
        for holder in (options, data):
            current = holder.get("pgn_include")
            if isinstance(current, str):
                current_list = [
                    int(p.strip())
                    for p in current.split(",")
                    if p.strip().isdigit()
                ]
                kept = [p for p in current_list if p not in ais_set]
                if len(kept) != len(current_list):
                    holder["pgn_include"] = ",".join(str(p) for p in kept)
                    entry_changed = True
                    removed = [p for p in current_list if p in ais_set]
                    messages.append(
                        f"nmea2000 entry {entry_id}: removed AIS PGNs {removed} "
                        f"from pgn_include (string form)."
                    )
            elif isinstance(current, list):
                kept = [p for p in current if p not in ais_set]
                if len(kept) != len(current):
                    removed = [p for p in current if p in ais_set]
                    holder["pgn_include"] = kept
                    entry_changed = True
                    messages.append(
                        f"nmea2000 entry {entry_id}: removed AIS PGNs {removed} "
                        f"from pgn_include (list form)."
                    )

        if not entry_changed:
            messages.append(
                f"nmea2000 entry {entry_id}: already AIS-free "
                "(exclude_AIS True, no AIS PGNs in pgn_include) — no change."
            )
        changed = changed or entry_changed

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
