#!/usr/bin/env python3
"""
cleanup_nmea_devices.py — Remove garbage NMEA 2000 devices & raw AIS entities from HA registry.

Run inside the HA container:
  docker exec homeassistant python3 /tmp/cleanup_nmea_devices.py [--dry-run|--dry-sensors] [--clean-ais] [--clean-all|--all]

Or run from host specifying config directory:
  python3 cleanup_nmea_devices.py --config-dir /path/to/ha/config [--dry-run|--dry-sensors] [--clean-ais] [--all]

Removes ALL devices linked to the nmea2000 config entry (or garbage devices + raw AIS entities)
so HA can rebuild clean from live N2K bus data.

Usage:
  python3 cleanup_nmea_devices.py               # removes garbage devices & orphaned entities
  python3 cleanup_nmea_devices.py --clean-sensors # removes garbage devices & orphaned entities
  python3 cleanup_nmea_devices.py --clean-ais    # additionally removes raw sensor.ais_* entities
  python3 cleanup_nmea_devices.py --all          # removes ALL nmea2000 devices (full reset)
  python3 cleanup_nmea_devices.py --dry-sensors  # dry-run mode
"""
import json
import os
import shutil
import sys
from datetime import datetime

BACKUP_SUFFIX   = f'.bak_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

# Manufacturer names created by our broken code (wrong manufacturer code 741 = Littelfuse)
GARBAGE_MANUFACTURERS = {'Littelfuse, Inc (formerly Selco Products Company)', 'None', ''}


def is_dry_run() -> bool:
    return '--dry-run' in sys.argv or '--dry-sensors' in sys.argv


def is_remove_all() -> bool:
    return '--all' in sys.argv or '--clean-all' in sys.argv


def is_clean_ais() -> bool:
    return '--clean-ais' in sys.argv or is_remove_all()


def resolve_config_dir() -> str:
    """Determine Home Assistant config directory location from CLI args, ENV, or default."""
    for i, arg in enumerate(sys.argv):
        if arg == '--config-dir' and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith('--config-dir='):
            return arg.split('=', 1)[1]

    if 'HA_CONFIG_DIR' in os.environ:
        return os.environ['HA_CONFIG_DIR']

    if os.path.exists('/config/.storage/core.config_entries'):
        return '/config'

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    dev_paths = [
        os.path.join(repo_root, 'ha', 'sailing-dash', 'local-ha', 'config'),
        os.path.join(repo_root, 'local-ha', 'config'),
    ]
    for dp in dev_paths:
        if os.path.exists(os.path.join(dp, '.storage', 'core.config_entries')):
            return dp

    return '/config'


def backup(path: str) -> str:
    bak = path + BACKUP_SUFFIX
    shutil.copy2(path, bak)
    print(f'  backup → {os.path.basename(bak)}')
    return bak


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def save(path: str, data: dict) -> None:
    if is_dry_run():
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
    """True if device belongs to our TCP-GW or nmea2000 and should be cleaned up."""
    linked = set(device.get('config_entries', [])) & nmea_entries
    if not linked:
        return False

    if is_remove_all():
        return True

    dev_str = str(device)
    # Safely match only our TCP-GW device entries (902047, 402047, legacy 12345, or PC Gateway)
    if any(k in dev_str for k in ('902047', '402047', '12345', 'PC Gateway')):
        return True

    return False


def main() -> None:
    dry_run = is_dry_run()
    remove_all = is_remove_all()
    clean_ais = is_clean_ais()

    print('=' * 60)
    print('NMEA 2000 Device & Entity Registry Cleanup')
    print(f'  mode: {"DRY RUN" if dry_run else "LIVE"}  '
          f'{"(remove ALL nmea2000 devices)" if remove_all else "(remove garbage/AIS)"}')
    print('=' * 60)

    config_dir    = resolve_config_dir()
    registry_path = os.path.join(config_dir, '.storage', 'core.device_registry')
    entity_path   = os.path.join(config_dir, '.storage', 'core.entity_registry')
    config_path   = os.path.join(config_dir, '.storage', 'core.config_entries')

    if not os.path.exists(config_path):
        print(f"Error: Home Assistant storage file not found: '{config_path}'.\n")
        print("This script is intended to run inside the Home Assistant Docker container:")
        print("  docker exec homeassistant python3 /tmp/cleanup_nmea_devices.py [--dry-run|--clean-ais|--all]\n")
        print("Or run from host machine via deploy script:")
        print("  ./deploy.sh --clean-ha\n")
        print("Or specify a custom HA configuration directory:")
        print("  python3 cleanup_nmea_devices.py --config-dir /path/to/ha/config")
        sys.exit(1)

    # Load registries
    config_data = load(config_path)
    device_data = load(registry_path)
    entity_data = load(entity_path)

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
            print(f'  REMOVE DEVICE id={d["id"][:8]}…  "{name}" [{mfr}]')
        else:
            to_keep.append(d)

    print(f'\n  Total devices: {len(devices_before)}')
    print(f'  To remove:     {len(to_remove_ids)}')
    print(f'  To keep:       {len(to_keep)}')

    # Identify entities to remove (linked to removed devices OR raw AIS entities if CLEAN_AIS is True)
    print('\n[3] Scanning entities...')
    entities_before  = entity_data['data']['entities']
    entities_keep    = []
    removed_entities = 0

    for e in entities_before:
        eid = e.get('entity_id', '')
        dev_id = e.get('device_id')
        entry_id = e.get('config_entry_id')

        is_ais = clean_ais and ('ais' in eid.lower()) and (entry_id in nmea_entries or e.get('platform') == 'nmea2000')
        is_orphaned = dev_id in to_remove_ids

        if is_orphaned or is_ais:
            removed_entities += 1
            reason = "orphaned" if is_orphaned else "raw AIS entity"
            print(f'  REMOVE ENTITY {eid} ({reason})')
        else:
            entities_keep.append(e)

    print(f'  Total entities before: {len(entities_before)}')
    print(f'  Removing entities:     {removed_entities}')

    if not to_remove_ids and removed_entities == 0:
        print('\nNothing to remove.')
        return

    # Apply changes
    print('\n[4] Writing registries...')
    device_data['data']['devices'] = to_keep
    entity_data['data']['entities'] = entities_keep

    save(registry_path, device_data)
    save(entity_path, entity_data)

    print('\n✓ Done. Restart Home Assistant to apply:')
    print('  docker restart homeassistant')


if __name__ == '__main__':
    main()
