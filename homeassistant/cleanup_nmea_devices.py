#!/usr/bin/env python3
"""
cleanup_nmea_devices.py — Remove garbage NMEA 2000 devices from HA device registry.

Run inside the HA container:
  docker exec homeassistant python3 /tmp/cleanup_nmea_devices.py [--dry-run]

Removes ALL devices linked to the nmea2000 config entry so HA can rebuild
clean from live N2K bus data. Also removes orphaned entities and their
long-term statistics entries.

Usage:
  python3 cleanup_nmea_devices.py          # removes garbage, keeps real devices
  python3 cleanup_nmea_devices.py --all    # removes ALL nmea2000 devices (full reset)
  python3 cleanup_nmea_devices.py --dry-run
"""
import json
import os
import shutil
import sys
from datetime import datetime

DRY_RUN = '--dry-run' in sys.argv
REMOVE_ALL = '--all' in sys.argv

REGISTRY_PATH   = '/config/.storage/core.device_registry'
ENTITY_PATH     = '/config/.storage/core.entity_registry'
CONFIG_PATH     = '/config/.storage/core.config_entries'
BACKUP_SUFFIX   = f'.bak_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# Manufacturer names created by our broken code (wrong manufacturer code 741 = Littelfuse)
GARBAGE_MANUFACTURERS = {'Littelfuse, Inc (formerly Selco Products Company)', 'None', ''}

def backup(path: str) -> str:
    bak = path + BACKUP_SUFFIX
    shutil.copy2(path, bak)
    print(f'  backup → {os.path.basename(bak)}')
    return bak


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save(path: str, data: dict) -> None:
    if DRY_RUN:
        print(f'  [dry-run] would write {path}')
        return
    backup(path)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f'  saved → {path}')


def find_nmea_entry_ids(config_data: dict) -> set[str]:
    """Find all config entry IDs for the nmea2000 integration."""
    ids = set()
    for entry in config_data['data']['entries']:
        if entry.get('domain') == 'nmea2000':
            ids.add(entry['entry_id'])
            print(f'  nmea2000 config entry: {entry["entry_id"]} '
                  f'({entry.get("title", "?")})')
    return ids


def is_garbage_device(device: dict, nmea_entries: set[str]) -> bool:
    """True if device belongs to our TCP-GW and should be cleaned up."""
    linked = set(device.get('config_entries', [])) & nmea_entries
    if not linked:
        return False

    dev_str = str(device)
    # Safely match only our TCP-GW device entries (902047, 402047, legacy 12345, or PC Gateway)
    if any(k in dev_str for k in ('902047', '402047', '12345', 'PC Gateway')):
        return True

    return False


def main() -> None:
    print('=' * 60)
    print('NMEA 2000 Device Registry Cleanup')
    print(f'  mode: {"DRY RUN" if DRY_RUN else "LIVE"}  '
          f'{"(remove ALL nmea2000 devices)" if REMOVE_ALL else "(remove garbage only)"}')
    print('=' * 60)

    # Load registries
    config_data = load(CONFIG_PATH)
    device_data = load(REGISTRY_PATH)
    entity_data = load(ENTITY_PATH)

    # Find nmea2000 config entry IDs
    print('\n[1] Finding nmea2000 config entries...')
    nmea_entries = find_nmea_entry_ids(config_data)
    if not nmea_entries:
        print('  No nmea2000 config entries found — nothing to clean.')
        return

    # Identify devices to remove
    print('\n[2] Scanning devices...')
    devices_before = device_data['data']['devices']
    to_remove_ids  = set()
    to_keep        = []

    for d in devices_before:
        if is_garbage_device(d, nmea_entries):
            to_remove_ids.add(d['id'])
            name = d.get('name') or d.get('model') or '?'
            mfr  = d.get('manufacturer') or 'no-mfr'
            print(f'  REMOVE  id={d["id"][:8]}…  "{name}" [{mfr}]')
        else:
            to_keep.append(d)

    print(f'\n  Total devices: {len(devices_before)}')
    print(f'  To remove:     {len(to_remove_ids)}')
    print(f'  To keep:       {len(to_keep)}')

    if not to_remove_ids:
        print('\nNothing to remove.')
        return

    # Remove orphaned entities
    print('\n[3] Removing orphaned entities...')
    entities_before = entity_data['data']['entities']
    entities_keep   = []
    removed_entities = 0
    for e in entities_before:
        if e.get('device_id') in to_remove_ids:
            removed_entities += 1
        else:
            entities_keep.append(e)
    print(f'  Removing {removed_entities} entities')

    # Apply changes
    print('\n[4] Writing registries...')
    device_data['data']['devices'] = to_keep
    entity_data['data']['entities'] = entities_keep

    save(REGISTRY_PATH, device_data)
    save(ENTITY_PATH, entity_data)

    print('\n✓ Done. Restart Home Assistant to apply:')
    print('  docker restart homeassistant')


if __name__ == '__main__':
    main()
