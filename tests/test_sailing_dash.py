"""Unit tests for ha/sailing-dash build system, NMEA emulator, and stage environment tools."""

import os
import sys
import json
import yaml
import time
import socket
import pytest
from unittest.mock import patch, Mock

# Add project root and ha/sailing-dash to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SAILING_DASH_DIR = os.path.join(PROJECT_ROOT, "ha", "sailing-dash")
LOCAL_HA_DIR = os.path.join(SAILING_DASH_DIR, "local-ha")

if SAILING_DASH_DIR not in sys.path:
    sys.path.insert(0, SAILING_DASH_DIR)
if LOCAL_HA_DIR not in sys.path:
    sys.path.insert(0, LOCAL_HA_DIR)

import build
from mock_nmea_emulator import fmt_nmea_line, NMEAEmulatorServer
import start_stage
import stage_provisioner
from stage_provisioner import HAProvisioner


def test_build_strip_leading_line_comments():
    """Test that strip_leading_line_comments removes initial // docstrings."""
    js_input = "// Header doc comment line 1\n// Header line 2\nconst x = 42;\nreturn x;"
    expected = "const x = 42;\nreturn x;"
    assert build.strip_leading_line_comments(js_input) == expected


def test_build_resolve_includes():
    """Test that resolve_includes substitutes $include:<name> placeholders recursively."""
    snippets = {"sample_snippet": "return 100;"}
    node = {
        "title": "Test Section",
        "card": {
            "code": "$include:sample_snippet",
            "list": ["$include:sample_snippet", "plain_string"],
        },
    }

    build.resolve_includes(node, snippets)

    assert node["card"]["code"] == "$fn return 100;"
    assert node["card"]["list"][0] == "$fn return 100;"
    assert node["card"]["list"][1] == "plain_string"


def test_build_pipeline_execution(tmp_path):
    """Test full build execution and verify YAML validity of compiled artifacts."""
    with patch.object(build, "BUILD_DIR", str(tmp_path)):
        build.ensure_dirs()
        build.build_cards()
        build.build_sensors()
        build.build_automations()
        build.build_resources()
        build.build_dashboard()

        # Check generated sensor file
        sensors_path = tmp_path / "sensors-sailing.yaml"
        assert sensors_path.exists()
        sensors_yaml = yaml.safe_load(sensors_path.read_text(encoding="utf-8"))
        assert isinstance(sensors_yaml, dict)

        # Check generated automations file
        automations_path = tmp_path / "automations-sailing.yaml"
        assert automations_path.exists()

        # Check generated resources file
        resources_path = tmp_path / "lovelace-resources.yaml"
        assert resources_path.exists()

        # Check generated dashboard file
        dashboard_path = tmp_path / "dashboard-sailing.yaml"
        assert dashboard_path.exists()


def test_mock_nmea_emulator_fmt_nmea_line():
    """Test that fmt_nmea_line constructs valid NMEA ASCII lines."""
    can_id = "09F50340"
    data_bytes = b"\x01\x02\x03\x04"
    line = fmt_nmea_line(can_id, data_bytes)

    parts = line.strip().split()
    assert len(parts) == 7
    assert parts[1] == "R"
    assert parts[2] == "09F50340"
    assert parts[3:] == ["01", "02", "03", "04"]


def test_mock_nmea_emulator_server_broadcast():
    """Test NMEAEmulatorServer client connection and broadcast loop."""
    # Find free port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()

    server = NMEAEmulatorServer(host="127.0.0.1", port=free_port)
    server.start()

    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", free_port))
        client.settimeout(2.0)

        # Receive onboarding frames
        received_data = b""
        start_time = time.time()
        while len(received_data) < 50 and (time.time() - start_time) < 3.0:
            try:
                chunk = client.recv(1024)
                if not chunk:
                    break
                received_data += chunk
            except socket.timeout:
                break

        assert b"18EEFF" in received_data
        client.close()
    finally:
        server.stop()


def test_start_stage_get_src_mtime():
    """Test get_src_mtime returns a valid timestamp for src/ directory."""
    mtime = start_stage.get_src_mtime()
    assert isinstance(mtime, float)
    assert mtime > 0


def test_stage_provisioner_inspect_empty_ha(tmp_path):
    """Test inspect_ha_environment on an empty HA config directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    provisioner = HAProvisioner(config_dir=str(config_dir))
    status = provisioner.inspect_ha_environment()

    assert status["is_clean_instance"] is True
    assert status["onboarding_done"] is False
    assert status["dashboard_registered"] is False
    assert len(status["missing_cards"]) > 0


def test_stage_provisioner_generate_registries(tmp_path):
    """Test onboarding bypass and storage registry provisioning on clean HA."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    provisioner = HAProvisioner(config_dir=str(config_dir))

    # Provision onboarding and registries
    assert provisioner.provision_onboarding() is True
    assert provisioner.provision_dashboard_registry() is True
    assert provisioner.provision_resource_registry() is True

    # Inspect again
    status = provisioner.inspect_ha_environment()
    assert status["onboarding_done"] is True
    assert status["dashboard_registered"] is True
    assert status["resources_registered"] is True


def test_stage_provisioner_passwordless_auth(tmp_path):
    """Test provision_auth creates a real test/test owner user via the standard
    'homeassistant' auth provider (with a valid bcrypt password hash)."""
    pytest.importorskip("bcrypt")
    import bcrypt

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    provisioner = HAProvisioner(config_dir=str(config_dir))
    assert provisioner.provision_auth(username="test", password="test") is True

    import base64
    import json
    auth_raw = provisioner.read_config_file(".storage/auth")
    auth = json.loads(auth_raw)

    users = auth["data"]["users"]
    owner = next((u for u in users if u.get("is_owner")), None)
    assert owner is not None

    credentials = auth["data"]["credentials"]
    ha_cred = next(
        (c for c in credentials if c.get("auth_provider_type") == "homeassistant"), None
    )
    assert ha_cred is not None
    assert ha_cred["user_id"] == owner["id"]
    # HA's own homeassistant.async_get_or_create_credentials() reads the username
    # from credential["data"]["username"] — must not be left as null.
    assert ha_cred["data"] == {"username": "test"}
    # trusted_networks credentials must never be left behind by a fresh provisioning run
    assert not any(c.get("auth_provider_type") == "trusted_networks" for c in credentials)

    provider_raw = json.loads(provisioner.read_config_file(".storage/auth_provider.homeassistant"))
    provider_user = next(
        (u for u in provider_raw["data"]["users"] if u["username"] == "test"), None
    )
    assert provider_user is not None
    stored_hash = base64.b64decode(provider_user["password"])
    assert bcrypt.checkpw(b"test", stored_hash)

    # Re-running provision_auth must not duplicate owners/credentials (idempotent)
    assert provisioner.provision_auth(username="test", password="test") is True
    auth_raw2 = json.loads(provisioner.read_config_file(".storage/auth"))
    assert len(auth_raw2["data"]["users"]) == len(users)
    assert len(auth_raw2["data"]["credentials"]) == len(credentials)
    provider_raw2 = json.loads(provisioner.read_config_file(".storage/auth_provider.homeassistant"))
    assert len(provider_raw2["data"]["users"]) == len(provider_raw["data"]["users"])


def test_stage_provisioner_card_bundle_resolution(tmp_path, monkeypatch):
    """Test full provisioning including card bundle deployment."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Run build to ensure build/cards exists
    build.build_cards()

    # HACS install involves a real network download (~50MB) — not something a unit
    # test suite should depend on; fake it here (still writing the manifest HACS
    # itself would produce) and verify the real download path separately/explicitly
    # in test_stage_provisioner_hacs_integration_deploy() with a mocked cache.
    def _fake_deploy_hacs(self):
        return self.write_config_file(
            "custom_components/hacs/manifest.json",
            json.dumps({"domain": "hacs", "name": "HACS", "version": "2.0.5", "config_flow": True}),
        )

    monkeypatch.setattr(stage_provisioner.HAProvisioner, "deploy_hacs_integration", _fake_deploy_hacs)

    provisioner = HAProvisioner(config_dir=str(config_dir))
    success = provisioner.run_full_provisioning()

    assert success is True
    status = provisioner.inspect_ha_environment()
    assert status["is_clean_instance"] is False
    assert len(status["missing_cards"]) == 0
    assert status["nmea2000_integration_installed"] is True
    assert status["nmea2000_configured"] is True


def test_stage_provisioner_hacs_integration_deploy(tmp_path, monkeypatch):
    """Test that deploy_hacs_integration() installs the real HACS integration
    (domain 'hacs') into /config/custom_components/hacs/, using a mocked/fake
    cached release so the test never depends on network access."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    fake_hacs_dir = tmp_path / "fake_hacs_cache"
    fake_hacs_dir.mkdir()
    (fake_hacs_dir / "manifest.json").write_text(
        json.dumps({"domain": "hacs", "name": "HACS", "version": "2.0.5", "config_flow": True})
    )
    (fake_hacs_dir / "__init__.py").write_text("# fake hacs __init__")

    monkeypatch.setattr(stage_provisioner, "HACS_CACHE_EXTRACTED_DIR", str(fake_hacs_dir))
    monkeypatch.setattr(stage_provisioner.HAProvisioner, "download_hacs_release", lambda self: True)

    provisioner = HAProvisioner(config_dir=str(config_dir))
    assert provisioner.deploy_hacs_integration() is True

    manifest_raw = provisioner.read_config_file("custom_components/hacs/manifest.json")
    assert manifest_raw is not None
    manifest = json.loads(manifest_raw)
    assert manifest["domain"] == "hacs"

    status = provisioner.inspect_ha_environment()
    assert status["hacs_installed"] is True


def test_stage_provisioner_nmea2000_integration_deploy(tmp_path):
    """Test that deploy_nmea2000_integration() copies the vendored custom_components/nmea2000
    integration files (domain 'nmea2000') into /config/custom_components/nmea2000/."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    provisioner = HAProvisioner(config_dir=str(config_dir))
    assert provisioner.deploy_nmea2000_integration() is True

    import json
    manifest_raw = provisioner.read_config_file("custom_components/nmea2000/manifest.json")
    assert manifest_raw is not None
    manifest = json.loads(manifest_raw)
    assert manifest["domain"] == "nmea2000"
    assert manifest["config_flow"] is True

    # Core integration files must all be present, not just the manifest.
    for filename in ["__init__.py", "config_flow.py", "const.py", "hub.py", "sensor.py"]:
        assert provisioner.read_config_file(f"custom_components/nmea2000/{filename}") is not None


def test_stage_provisioner_nmea2000_config_entry(tmp_path):
    """Test provision_nmea2000_config_entry() registers a TEXT/TCP gateway config entry
    pointed at the local mock_nmea_emulator.py, and that it's idempotent."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    provisioner = HAProvisioner(config_dir=str(config_dir))
    assert provisioner.provision_nmea2000_config_entry(host="127.0.0.1", port=4001) is True

    import json
    entries_raw = json.loads(provisioner.read_config_file(".storage/core.config_entries"))
    entries = entries_raw["data"]["entries"]
    nmea_entries = [e for e in entries if e.get("domain") == "nmea2000"]
    assert len(nmea_entries) == 1
    entry = nmea_entries[0]
    assert entry["data"]["gateway_type"] == "text"
    assert entry["data"]["ip"] == "127.0.0.1"
    assert entry["data"]["port"] == 4001

    # Re-running must not create a second entry (idempotent).
    assert provisioner.provision_nmea2000_config_entry(host="127.0.0.1", port=4001) is True
    entries_raw2 = json.loads(provisioner.read_config_file(".storage/core.config_entries"))
    nmea_entries2 = [e for e in entries_raw2["data"]["entries"] if e.get("domain") == "nmea2000"]
    assert len(nmea_entries2) == 1
    assert nmea_entries2[0]["entry_id"] == entry["entry_id"]
