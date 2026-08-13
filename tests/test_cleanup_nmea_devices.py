import json
import os
import sys
import pytest

from homeassistant.cleanup_nmea_devices import (
    resolve_config_dir,
    find_nmea_entry_ids,
    is_garbage_device,
    is_dry_run,
    is_remove_all,
    main,
)


def test_resolve_config_dir_cli(monkeypatch, tmp_path):
    custom_dir = str(tmp_path / "custom_ha")
    monkeypatch.setattr(sys, "argv", ["cleanup_nmea_devices.py", "--config-dir", custom_dir])
    assert resolve_config_dir() == custom_dir


def test_resolve_config_dir_env(monkeypatch, tmp_path):
    env_dir = str(tmp_path / "env_ha")
    monkeypatch.setattr(sys, "argv", ["cleanup_nmea_devices.py"])
    monkeypatch.setenv("HA_CONFIG_DIR", env_dir)
    assert resolve_config_dir() == env_dir


def test_missing_config_file_exit(monkeypatch, tmp_path):
    missing_dir = str(tmp_path / "nonexistent")
    monkeypatch.setattr(sys, "argv", ["cleanup_nmea_devices.py", "--config-dir", missing_dir])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_cleanup_nmea_devices_execution(monkeypatch, tmp_path):
    config_dir = tmp_path / "ha_config"
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True)

    config_entries = {
        "data": {
            "entries": [
                {"entry_id": "nmea_entry_1", "domain": "nmea2000", "title": "NMEA 2000"}
            ]
        }
    }
    device_registry = {
        "data": {
            "devices": [
                {
                    "id": "dev_garbage",
                    "name": "PC Gateway",
                    "manufacturer": "None",
                    "config_entries": ["nmea_entry_1"],
                },
                {
                    "id": "dev_real",
                    "name": "Real NMEA Sensor",
                    "manufacturer": "Garmin",
                    "config_entries": ["nmea_entry_1"],
                },
            ]
        }
    }
    entity_registry = {
        "data": {
            "entities": [
                {
                    "entity_id": "sensor.boat_speed",
                    "device_id": "dev_real",
                    "config_entry_id": "nmea_entry_1",
                    "platform": "nmea2000",
                },
                {
                    "entity_id": "sensor.ais_123456789_position",
                    "device_id": "dev_real",
                    "config_entry_id": "nmea_entry_1",
                    "platform": "nmea2000",
                },
                {
                    "entity_id": "sensor.garbage_entity",
                    "device_id": "dev_garbage",
                    "config_entry_id": "nmea_entry_1",
                    "platform": "nmea2000",
                },
            ]
        }
    }

    with open(storage_dir / "core.config_entries", "w") as f:
        json.dump(config_entries, f)
    with open(storage_dir / "core.device_registry", "w") as f:
        json.dump(device_registry, f)
    with open(storage_dir / "core.entity_registry", "w") as f:
        json.dump(entity_registry, f)

    # Run cleanup with --clean-ais
    monkeypatch.setattr(
        sys,
        "argv",
        ["cleanup_nmea_devices.py", "--config-dir", str(config_dir), "--clean-ais"],
    )
    main()

    # Load result
    with open(storage_dir / "core.device_registry") as f:
        res_devices = json.load(f)["data"]["devices"]
    with open(storage_dir / "core.entity_registry") as f:
        res_entities = json.load(f)["data"]["entities"]

    # Garbage device removed, real device kept
    assert len(res_devices) == 1
    assert res_devices[0]["id"] == "dev_real"

    # Garbage entity and AIS entity removed, real boat speed entity kept
    assert len(res_entities) == 1
    assert res_entities[0]["entity_id"] == "sensor.boat_speed"


def test_flags_dry_sensors_and_clean_all(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["cleanup_nmea_devices.py", "--dry-sensors", "--clean-all"])
    assert is_dry_run() is True
    assert is_remove_all() is True
