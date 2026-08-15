#!/usr/bin/env python3
"""
verify_ais_targets.py — drift-guard confirming the AIS PGN set is present in
the currently-installed `nmea2000` package's `pgns.py`.

Mirrors the `verify_nmea2000_fork()` pattern in the root deploy.sh (see
.agents/skills/nmea2000-setup/SKILL.md): rather than patching anything, this
only checks that what is actually installed matches what this integration
was built against, and prints exactly what is missing/what to do about it.

USAGE
    python3 verify_ais_targets.py                 # check THIS interpreter's
                                                    # installed nmea2000
    python3 verify_ais_targets.py --pgns-file PATH # check pgns.py directly
                                                    # (e.g. one fetched from a
                                                    # container by deploy.sh)

Exit code 0 = all AIS PGNs found, 1 = one or more missing / nmea2000 not
importable, matching the convention used by verify_nmea2000_fork() (0 = OK,
non-zero = drift, callers typically `|| true` this so a partial mismatch
does not abort an entire deploy pipeline).
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import os
import re
import sys

# Same PGN set as helpers/patch_pgn_include.py — kept in this separate list
# (not imported from there) so this script has zero dependency on anything
# except the Python standard library and can run standalone inside a
# container that only has nmea2000 installed, no project code.
AIS_PGNS = [129038, 129039, 129040, 129041, 129793, 129794, 129809, 129810]


def find_local_pgns_path() -> str | None:
    """Return the path of the pgns.py belonging to the currently-importable
    `nmea2000` package, or None if it cannot be imported."""
    try:
        nmea2000 = importlib.import_module("nmea2000")
    except ImportError:
        return None

    pkg_dir = os.path.dirname(inspect.getfile(nmea2000))
    candidate = os.path.join(pkg_dir, "pgns.py")
    return candidate if os.path.isfile(candidate) else None


def check_pgns_file(path: str) -> tuple[list[int], list[int]]:
    """Return `(found, missing)` PGNs from AIS_PGNS, searched for as bare
    integers in the given pgns.py source (a plain textual grep, matching the
    marker-based approach `verify_nmea2000_fork()` uses for its two fix
    markers — good enough since PGN ids are declared as int literals)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    found = []
    missing = []
    for pgn in AIS_PGNS:
        # \b boundaries avoid 129038 matching inside e.g. 1290381.
        if re.search(rf"\b{pgn}\b", text):
            found.append(pgn)
        else:
            missing.append(pgn)
    return found, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pgns-file",
        help="path to a pgns.py to check directly, instead of importing "
        "nmea2000 in this interpreter (use this for a copy fetched out of "
        "a container — see the comment block in ha/ais/deploy.sh)",
    )
    args = parser.parse_args()

    if args.pgns_file:
        pgns_path = args.pgns_file
        if not os.path.isfile(pgns_path):
            print(f"ERROR: {pgns_path} does not exist", file=sys.stderr)
            return 1
    else:
        pgns_path = find_local_pgns_path()
        if pgns_path is None:
            print(
                "WARNING: nmea2000 is not importable in this interpreter — "
                "cannot verify AIS PGN support. Activate the venv/container "
                "that has it installed, or pass --pgns-file explicitly.",
                file=sys.stderr,
            )
            return 1

    found, missing = check_pgns_file(pgns_path)

    print(f"Checked {pgns_path}")
    print(f"  AIS PGNs found:   {found}")
    if missing:
        print(f"  AIS PGNs MISSING: {missing}", file=sys.stderr)
        print(
            "DRIFT: the installed nmea2000 package does not decode all AIS "
            "PGNs this integration relies on. Update the nmea2000 dependency "
            "(requirements.txt / the HA nmea2000 integration's manifest.json) "
            "to a version whose pgns.py defines them, then re-run this check.",
            file=sys.stderr,
        )
        return 1

    print("OK — all AIS PGNs are present in this nmea2000 package. ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
