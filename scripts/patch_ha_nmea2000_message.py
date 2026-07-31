#!/usr/bin/env python3
"""patch_ha_nmea2000_message.py — Idempotent fix for nmea2000/message.py hash collision bug.
==============================================================================================

WHAT THIS PATCH DOES:
  Fixes Bug 2 in the nmea2000 library shipped inside the Home Assistant Docker container.

  The bug: `add_data()` in NMEA2000Message computes `primary_key = f"{self.id}"` without
  including the source device identity. For PGN 126996 (Product Information), self.id is
  always "productInformation" and no field has part_of_primary_key=True, so ALL devices on
  the NMEA 2000 bus produce the same MD5 hash:
      hashlib.md5(b"productInformation").hexdigest() == "818d9516db08fd90ffd1967e3c403bed"

  This causes the second device card in Home Assistant to receive 0 entities
  ("This device has no entities"), because ha-nmea2000 uses message.hash to build
  sensor IDs — identical hashes collapse all sensors into the first device's card.

  The fix: include source_iso_name.unique_number (21-bit unique number, stable per
  NMEA 2000 spec §3.1.1) in primary_key. This is manufacturer-assigned and never
  changes, unlike iso_name.name (64-bit) which includes device_instance and changes
  on bus reinitialisation — causing a new HA device entry on every gateway restart.

PATCH VERSION HISTORY:
  v1 (yacht-n2k-console-patch-v1):
    Used source_iso_name.name (full 64-bit ISO NAME integer). This fixed the hash
    collision but introduced a new bug: iso_name.name includes device_instance, which
    YDNU-02 changes on every bus reinitialisation. Each restart produced a different
    MD5, creating a new device entry in HA registry. After N restarts, N duplicate
    "Product Information (Yacht Devices - PC Gateway - 402047)" devices appeared in HA.

  v2 (yacht-n2k-console-patch-v2) — CURRENT:
    Uses source_iso_name.unique_number (bits 63-43 of ISO NAME, manufacturer-assigned).
    This is truly stable — does not change on bus events. Result: hash is the same
    across all gateway restarts, HA device entry is reused (not recreated).
    Stable hashes:
      SA=64  unique_number=402047 → ef195c7c99c762fdfda4e198aae87930
      SA=200 unique_number=902047 → c11f5c824c71fe7e186cba56bf0f8672
    Upgrade v1→v2 is handled automatically: see main() upgrade path.

IDEMPOTENCY:
  Three states handled:
    A. PATCH_MARKER_V2 found → already up to date, nothing to do.
    B. PATCH_MARKER_V1 found → upgrade: replace .name → .unique_number, bump marker.
    C. Neither found (fresh upstream) → fresh install from ORIGINAL_KEY_LINE.
  Running this script multiple times is always safe.

HA REGISTRY CLEANUP:
  After upgrading from v1 to v2, existing stale device records (with old hashes) remain
  in HA. Run `./deploy.sh --clean-ha` once to purge them. After that, HA will recreate
  devices with v2-stable hashes and no further cleanup is needed.

USAGE (inside HA Docker container):
  python3 /tmp/patch_ha_nmea2000_message.py

USAGE (from Mac via SSH — used by deploy.sh):
  ssh user@gateway-host "sudo docker cp /tmp/patch_ha_nmea2000_message.py homeassistant:/tmp/ && \\
    sudo docker exec homeassistant python3 /tmp/patch_ha_nmea2000_message.py"

RELATED:
  - nmea2000/message.py in local repo: identical fix in our fork (dnevera/nmea2000 branch).
  - nmea2000/decoder.py: documents the complementary "silent drop" bug (source_to_iso_name).
  - ydnu02_tcp_gateway/data_hub.py: ANNOUNCE_PRODUCT_INFO_DELAY ensures Phase 1 (PGN 60928)
    is processed before Phase 2 (PGN 126996) so source_to_iso_name is ready.
  - deploy.sh patch_ha(): drives Patch 1 (ioclient EOF) + Patch 2 (this script).
"""

import re
import sys
import shutil
import hashlib
from pathlib import Path

import subprocess


def _discover_target() -> Path:
    """Discover nmea2000/message.py path inside this Python environment dynamically.

    Uses the same pattern as deploy.sh patch_ha() for ioclient.py:
      python3 -c 'import nmea2000.message as m; print(m.__file__)'

    This survives Python version bumps in the HA Docker image (e.g. 3.14 → 3.15).
    """
    try:
        result = subprocess.run(
            ["python3", "-c", "import nmea2000.message as m; print(m.__file__)"],
            capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError as exc:
        print(f"[patch] ERROR: cannot locate nmea2000.message: {exc.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


TARGET = _discover_target()
PATCH_MARKER = "yacht-n2k-console-patch-v2"
PATCH_MARKER_V1 = "yacht-n2k-console-patch-v1"  # old marker — needs upgrade to v2

# The exact original lines to find (as they appear in the unpatched OR v1-patched file)
ORIGINAL_KEY_LINE = '            primary_key = f"{self.id}"\n'
V1_SOURCE_ID_LINE = '                self.source_iso_name.name\n'  # v1 used .name (unstable)

# Replacement block: use unique_number (stable, manufacturer-assigned per NMEA 2000 §3.1.1)
# NOT iso_name.name which includes device_instance (changes on bus reinitialisation).
REPLACEMENT_BLOCK = (
    '            # primary_key = f"{self.id}"  # original upstream (hash collision bug)\n'
    "            #\n"
    f"            # {PATCH_MARKER}\n"
    "            # NOTE (yacht-n2k-console fix): The line above caused ALL devices to share\n"
    "            # the same MD5 for PGNs like 126996 where self.id is constant and no field\n"
    "            # has part_of_primary_key=True. Fix: include source identity in primary_key.\n"
    "            # WHY unique_number, NOT iso_name.name:\n"
    "            #   unique_number: 21-bit, manufacturer-assigned (NMEA 2000 §3.1.1), stable.\n"
    "            #   iso_name.name: 64-bit integer including device_instance, which changes\n"
    "            #   on bus reinitialisation → different hash every restart → HA duplicates.\n"
    "            # See: ydnu02_tcp_gateway/data_hub.py → ANNOUNCE_PRODUCT_INFO_DELAY\n"
    "            source_id = (\n"
    "                self.source_iso_name.unique_number\n"
    "                if self.source_iso_name is not None\n"
    "                else self.source\n"
    "            )\n"
    '            primary_key = f"{self.id}_{source_id}"\n'
)


def main() -> int:
    if not TARGET.exists():
        print(f"[patch] ERROR: target file not found: {TARGET}", file=sys.stderr)
        return 1

    content = TARGET.read_text(encoding="utf-8")

    # --- Idempotency check: v2 already applied ---
    if PATCH_MARKER in content:
        print(f"[patch] Already applied ({PATCH_MARKER}). Nothing to do.")
        return 0

    # --- Upgrade path: v1 → v2 (replace .name with .unique_number) ---
    if PATCH_MARKER_V1 in content:
        print(f"[patch] Found old {PATCH_MARKER_V1} — upgrading to {PATCH_MARKER}...")
        if V1_SOURCE_ID_LINE not in content:
            print("[patch] ERROR: v1 source_id line not found. Manual review required.", file=sys.stderr)
            return 1
        backup = TARGET.with_suffix(".py.pre-yacht-patch-v2")
        shutil.copy2(TARGET, backup)
        print(f"[patch] Backup written to: {backup}")
        new_content = content.replace(
            V1_SOURCE_ID_LINE,
            "                self.source_iso_name.unique_number\n",
            1,
        ).replace(PATCH_MARKER_V1, PATCH_MARKER, 1)
        TARGET.write_text(new_content, encoding="utf-8")
        print(f"[patch] SUCCESS: upgraded to {PATCH_MARKER}")
        return 0

    # --- Fresh install: verify the original upstream line is present ---
    if ORIGINAL_KEY_LINE not in content:
        print(
            "[patch] ERROR: expected original line not found in target file.\n"
            "        The library version may have changed. Manual review required.",
            file=sys.stderr,
        )
        print(f"[patch] Looking for: {ORIGINAL_KEY_LINE!r}", file=sys.stderr)
        return 1

    # --- Backup ---
    backup = TARGET.with_suffix(".py.pre-yacht-patch")
    shutil.copy2(TARGET, backup)
    print(f"[patch] Backup written to: {backup}")

    # --- Apply ---
    new_content = content.replace(ORIGINAL_KEY_LINE, REPLACEMENT_BLOCK, 1)

    if new_content == content:
        print("[patch] ERROR: replacement produced no change. Aborting.", file=sys.stderr)
        return 1

    TARGET.write_text(new_content, encoding="utf-8")

    # --- Verify ---
    verify = TARGET.read_text(encoding="utf-8")
    if PATCH_MARKER not in verify:
        print("[patch] ERROR: patch marker not found after write. File may be corrupt.", file=sys.stderr)
        return 1

    print(f"[patch] SUCCESS: {TARGET} patched.")
    print(f"[patch] Marker: {PATCH_MARKER}")
    print("[patch] Restart Home Assistant integration 'nmea2000' to apply changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
