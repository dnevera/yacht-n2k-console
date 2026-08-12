"""Unit tests for ha/sailing-dash build system, NMEA emulator, and stage environment tools."""

import os
import re
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
# Everything that is not an entry point lives in ha/sailing-dash/helpers/.
HELPERS_DIR = os.path.join(SAILING_DASH_DIR, "helpers")
LOCAL_HA_DIR = os.path.join(SAILING_DASH_DIR, "local-ha")

if HELPERS_DIR not in sys.path:
    sys.path.insert(0, HELPERS_DIR)
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


def test_build_resources_content_hashed_cache_buster(tmp_path):
    """Local `/local/*.js` resources must get a content-hash `?v=`, not a static one.

    Regression for a real stale-browser-cache bug: our own card entries in
    lovelace-resources.yaml carried a hand-written `?v=1.0.0` that never
    changed when the JS file's content did, so the browser's ES-module
    cache kept serving old bytes for an already-updated card. Third-party
    HACS resources (no matching file under src/js/cards/) must be left
    untouched — only cards we own and control the version scheme for.
    """
    import hashlib as _hashlib

    with patch.object(build, "BUILD_DIR", str(tmp_path)):
        build.ensure_dirs()
        build.build_resources()

        resources_yaml = yaml.safe_load(
            (tmp_path / "lovelace-resources.yaml").read_text(encoding="utf-8")
        )

    items = {item["url"].split("?", 1)[0]: item["url"] for item in resources_yaml["resources"]}

    card_path = os.path.join(SAILING_DASH_DIR, "src", "js", "cards", "windy-boat-card.js")
    with open(card_path, "rb") as f:
        expected_hash = _hashlib.sha256(f.read()).hexdigest()[:8]

    assert items["/local/windy-boat-card.js"] == (
        f"/local/windy-boat-card.js?v={expected_hash}"
    )
    # Third-party HACS resource without a matching local card file stays as-is.
    assert items["/local/windrose-card.js"] == "/local/windrose-card.js?v=2.4.2"


def _collect_unique_ids(sensors_yaml):
    """Collect 'sensor.<unique_id>'-style entity ids from a compiled sensors artifact."""
    entities = set()
    for entries in sensors_yaml.values():
        if not isinstance(entries, list):
            entries = [entries]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for domain, items in entry.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and "unique_id" in item:
                        entities.add(f"{domain}.{item['unique_id']}")
    return entities


def test_build_dashboard_entities_are_all_defined(tmp_path):
    """Every sensor the dashboard references must exist in the compiled sensors.

    Guards the regression where splitting the monolithic sensors-sailing.yaml
    into src/yaml/sensors/ modules dropped the forecast template sensors
    (chart_time_window, wind/wave *_flat and *_next_hour), leaving the
    dashboard pointing at entities that were never defined.
    """
    with patch.object(build, "BUILD_DIR", str(tmp_path)):
        build.ensure_dirs()
        build.build_sensors()
        build.build_dashboard()

        sensors_yaml = yaml.safe_load((tmp_path / "sensors-sailing.yaml").read_text(encoding="utf-8"))
        defined = _collect_unique_ids(sensors_yaml)

        dashboard_text = (tmp_path / "dashboard-sailing.yaml").read_text(encoding="utf-8")
        referenced = set(re.findall(r"\bsensor\.[a-z0-9_]+", dashboard_text))

    # Raw NMEA 2000 entities (…_pk_<hash>_…) come from the integration itself,
    # not from our template sensors, so they are not expected to be defined here.
    referenced = {e for e in referenced if "_pk_" not in e}

    assert referenced, "dashboard references no sensors — the build produced nothing"
    assert not (referenced - defined), (
        f"dashboard references undefined sensors: {sorted(referenced - defined)}"
    )


def test_build_sensors_merges_duplicate_top_level_keys(tmp_path):
    """Sensor modules sharing a top-level key must be merged, not overwrite each other."""
    with patch.object(build, "BUILD_DIR", str(tmp_path)):
        build.ensure_dirs()
        build.build_sensors()
        sensors_yaml = yaml.safe_load((tmp_path / "sensors-sailing.yaml").read_text(encoding="utf-8"))

    defined = _collect_unique_ids(sensors_yaml)
    # derived_n2k.yaml and forecast.yaml both declare `template:`
    assert "sensor.boat_depth" in defined
    assert "sensor.chart_time_window" in defined
    assert "sensor.wind_forecast_rest" in defined


SENSORS_SRC_DIR = os.path.join(SAILING_DASH_DIR, "src", "yaml", "sensors")


def _template_sensors(fname):
    with open(os.path.join(SENSORS_SRC_DIR, fname), encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for block in data.get("template", []):
        out.extend(block.get("sensor", []))
    return out


def test_derived_sensors_are_unavailable_instead_of_reporting_zero():
    """A missing N2K source must NOT become a hard 0.

    `{{ states(src) | float(0) }}` without `availability` makes the alias report
    0 forever while the bus is quiet: the wind chart then draws a flat zero line
    and open-meteo gets asked about 0N/0E. Every alias reading a raw entity has
    to declare availability on that same entity.

    Exception: `boat_magnetic_variation` defaults to 0.0 when variation is absent
    on the bus so heading calculations work on compasses that do not output variation.
    """
    for sensor in _template_sensors("derived_n2k.yaml"):
        if sensor.get("unique_id") == "boat_magnetic_variation":
            continue
        state = sensor.get("state", "")
        srcs = re.findall(r"states\('(sensor\.[a-z0-9_]+)'\)", state)
        raw_srcs = [s for s in srcs if not s.startswith("sensor.boat_")]
        if not raw_srcs or "float(0)" not in state:
            continue
        availability = sensor.get("availability", "")
        assert availability, f"{sensor['unique_id']} has no availability template"
        for src in raw_srcs:
            assert src in availability, (
                f"{sensor['unique_id']} defaults to 0 when {src} is missing"
            )


def test_cog_and_sog_do_not_read_the_cog_reference_enum():
    """PGN 129026 exposes `_cog_reference` (True/Magnetic enum) next to `_cog`.

    A loose entity match used to pick the enum for BOTH Boat COG and Boat SOG.
    """
    by_id = {s["unique_id"]: s for s in _template_sensors("derived_n2k.yaml")}
    assert "_cog_reference" not in by_id["boat_cog"]["state"]
    assert "_cog" in by_id["boat_cog"]["state"]
    assert "_sog" in by_id["boat_sog"]["state"]


def test_map_nmea_sensors_matches_cog_and_sog_exactly():
    """The generator itself must not fall for the `_cog_reference` entity."""
    import map_nmea_sensors

    entities = [
        "sensor.cog_sog_rapid_update_x_cog_reference",
        "sensor.cog_sog_rapid_update_x_cog",
        "sensor.cog_sog_rapid_update_x_sog",
    ]
    discovered = map_nmea_sensors.match_entities(entities)
    assert discovered["cog"] == "sensor.cog_sog_rapid_update_x_cog"
    assert discovered["sog"] == "sensor.cog_sog_rapid_update_x_sog"


def test_open_meteo_falls_back_to_the_home_port_without_a_gps_fix():
    """`| float(42.43)` never fires for a 0.0 position, hence the explicit check.

    Without it the forecast is fetched for 0N/0E (Gulf of Guinea) whenever the
    boat's position aliases hold zero, and the wind chart shows a plausible but
    completely unrelated forecast.
    """
    with open(os.path.join(SENSORS_SRC_DIR, "open_meteo.yaml"), encoding="utf-8") as f:
        rest_blocks = (yaml.safe_load(f) or {}).get("rest", [])
    assert rest_blocks
    for block in rest_blocks:
        template = block["resource_template"]
        assert "float(42.43)" not in template
        assert "42.43" in template and "18.60" in template
        assert "abs < 0.01" in template
        # The rendered value must stay a single-line URL.
        assert "latitude={{ lat }}" in template


HA_TARGET_LIB = os.path.join(HELPERS_DIR, "lib", "ha_target.sh")


def _run_ha_target_snippet(tmp_path, snippet):
    """Run a bash snippet against lib/ha_target.sh with the container faked out.

    ha_cat / ha_cp_to_container / ha_cp_dir_to_container are replaced by plain
    filesystem operations under FAKE_ROOT, so the delivery logic (what gets
    copied and what is skipped) is testable without Docker or SSH.
    """
    import subprocess
    import textwrap

    script = tmp_path / "case.sh"
    script.write_text(
        textwrap.dedent(
            f"""
            set -euo pipefail
            source "{HA_TARGET_LIB}"
            HA_CONTAINER=fake
            FAKE_ROOT="{tmp_path}/container"
            mkdir -p "${{FAKE_ROOT}}"

            ha_cat() {{ cat "${{FAKE_ROOT}}$1"; }}
            ha_mkdir() {{ mkdir -p "${{FAKE_ROOT}}$1"; }}
            ha_cp_to_container() {{ mkdir -p "$(dirname "${{FAKE_ROOT}}$2")"; cp "$1" "${{FAKE_ROOT}}$2"; }}
            ha_cp_dir_to_container() {{ mkdir -p "${{FAKE_ROOT}}$2"; cp -R "${{1%/}}/." "${{FAKE_ROOT}}$2"; }}
            """
        )
        + textwrap.dedent(snippet),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, cwd=str(tmp_path)
    )
    assert proc.returncode == 0, f"snippet failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def test_ha_target_file_delivery_skips_identical_content(tmp_path):
    """A file whose content already matches the container's must not be copied."""
    local = tmp_path / "card.js"
    local.write_text("console.log('v1');\n", encoding="utf-8")

    out = _run_ha_target_snippet(
        tmp_path,
        """
        ha_cp_to_container_if_changed "%s" "/config/www/card.js" && echo "FIRST=copied"
        ha_cp_to_container_if_changed "%s" "/config/www/card.js" || echo "SECOND=skipped"
        echo "DELIVERED=${HA_DELIVERED} SKIPPED=${HA_SKIPPED}"
        """
        % (local, local),
    )

    assert "FIRST=copied" in out
    assert "SECOND=skipped" in out
    assert "DELIVERED=1 SKIPPED=1" in out


def test_ha_target_file_delivery_detects_changed_content(tmp_path):
    """Changed local content (and --force) must still trigger a copy."""
    local = tmp_path / "card.js"
    local.write_text("console.log('v1');\n", encoding="utf-8")
    other = tmp_path / "card_v2.js"
    other.write_text("console.log('v2');\n", encoding="utf-8")

    out = _run_ha_target_snippet(
        tmp_path,
        """
        ha_cp_to_container_if_changed "%s" "/config/www/card.js" >/dev/null
        ha_cp_to_container_if_changed "%s" "/config/www/card.js" && echo "CHANGED=copied"
        HA_FORCE_DELIVERY=1 ha_cp_to_container_if_changed "%s" "/config/www/card.js" && echo "FORCED=copied"
        """
        % (local, other, other),
    )

    assert "CHANGED=copied" in out
    assert "FORCED=copied" in out


def test_ha_target_dir_delivery_skips_unchanged_tree(tmp_path):
    """A directory is re-delivered only when its tree manifest changed."""
    src = tmp_path / "integration"
    src.mkdir()
    (src / "manifest.json").write_text('{"domain": "nmea2000"}\n', encoding="utf-8")
    (src / "sensor.py").write_text("X = 1\n", encoding="utf-8")

    out = _run_ha_target_snippet(
        tmp_path,
        """
        ha_state_load
        ha_cp_dir_to_container_if_changed "%s" "/config/custom_components/nmea2000" && echo "FIRST=copied"
        ha_state_flush
        HA_STATE_FILE="" HA_STATE_DIRTY=0
        ha_state_load
        ha_cp_dir_to_container_if_changed "%s" "/config/custom_components/nmea2000" || echo "SECOND=skipped"
        """
        % (src, src),
    )

    assert "FIRST=copied" in out
    assert "SECOND=skipped" in out
    # The state survived a "new deploy run" because it lives in the container.
    assert (tmp_path / "container" / "config" / ".storage" / "sailing_deploy_state").exists()
    assert (tmp_path / "container" / "config" / "custom_components" / "nmea2000" / "manifest.json").exists()


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


def test_stage_provisioner_copy_card_skips_identical_content(tmp_path):
    """copy_card_to_ha must not re-copy a file the target already holds."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    src = tmp_path / "windy-boat-card.js"
    src.write_text("console.log('v1');\n", encoding="utf-8")

    provisioner = HAProvisioner(config_dir=str(config_dir))

    assert provisioner.copy_card_to_ha(str(src), "www/windy-boat-card.js") is True
    assert provisioner.last_copy_skipped is False

    assert provisioner.copy_card_to_ha(str(src), "www/windy-boat-card.js") is True
    assert provisioner.last_copy_skipped is True
    assert provisioner.delivered_count == 1
    assert provisioner.skipped_count == 1

    src.write_text("console.log('v2');\n", encoding="utf-8")
    assert provisioner.copy_card_to_ha(str(src), "www/windy-boat-card.js") is True
    assert provisioner.last_copy_skipped is False
    assert (config_dir / "www" / "windy-boat-card.js").read_text() == "console.log('v2');\n"


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
    (domain 'hacs') into /config/custom_components/hacs/, using a fake
    build/deps/ artifact so the test never depends on network access."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    fake_hacs_dir = tmp_path / "fake_hacs_cache"
    fake_hacs_dir.mkdir()
    (fake_hacs_dir / "manifest.json").write_text(
        json.dumps({"domain": "hacs", "name": "HACS", "version": "2.0.5", "config_flow": True})
    )
    (fake_hacs_dir / "__init__.py").write_text("# fake hacs __init__")

    monkeypatch.setattr(stage_provisioner, "HACS_INTEGRATION_DEPS_DIR", str(fake_hacs_dir))
    monkeypatch.setattr(stage_provisioner.HAProvisioner, "fetch_dependency",
                        staticmethod(lambda section, dest_dir: True))

    provisioner = HAProvisioner(config_dir=str(config_dir))
    assert provisioner.deploy_hacs_integration() is True

    manifest_raw = provisioner.read_config_file("custom_components/hacs/manifest.json")
    assert manifest_raw is not None
    manifest = json.loads(manifest_raw)
    assert manifest["domain"] == "hacs"

    status = provisioner.inspect_ha_environment()
    assert status["hacs_installed"] is True


def test_stage_provisioner_nmea2000_integration_deploy(tmp_path):
    """Test that deploy_nmea2000_integration() copies the custom_components/nmea2000
    integration downloaded from our fork's pinned tag into build/deps/ (never a
    vendored copy) into /config/custom_components/nmea2000/."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    if not os.path.isfile(os.path.join(
            stage_provisioner.NMEA2000_INTEGRATION_DEPS_DIR, "manifest.json")):
        pytest.skip("build/deps/ not populated — run `python3 fetch_deps.py` first")

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


BUILT_DASHBOARD = os.path.join(SAILING_DASH_DIR, "build", "dashboard-sailing.yaml")
JS_SNIPPET_TEST = os.path.join(PROJECT_ROOT, "tests", "js", "wind_chart_snippets.test.js")


def _wind_chart_card():
    """The `custom:plotly-graph` wind vector card from the built dashboard."""
    with open(BUILT_DASHBOARD, encoding="utf-8") as f:
        dashboard = yaml.safe_load(f)
    for section in dashboard["views"][0]["sections"]:
        for card in section.get("cards", []):
            if card.get("type") != "custom:plotly-graph":
                continue
            if "Wind speed" in str(card.get("layout", {}).get("yaxis", {}).get("title", "")):
                return card
    raise AssertionError("wind vector chart not found in the built dashboard")


def test_wind_chart_drops_non_numeric_samples_before_resampling():
    """Recorder history is not purely numeric — NaN must never reach `resample`.

    `unknown` (HA restart) and `unavailable` (a derived alias while the N2K bus
    is quiet) become NaN under `map_y: parseFloat(y)`; a single NaN inside a
    resample bucket propagates into the trace, the `$ex ys` colour scale and the
    autoranged Y axis, which is what made the chart show nonsense.
    """
    card = _wind_chart_card()
    checked = 0
    for series in card["entities"]:
        steps = series.get("filters", [])
        keys = [list(step)[0] for step in steps]
        if "map_y" not in keys:
            continue  # forecast series: values come from attributes, not history
        checked += 1
        drop_at = next(
            (i for i, step in enumerate(steps)
             if list(step)[0] == "fn" and "Number.isFinite" in step["fn"]),
            None,
        )
        assert drop_at is not None, f"{series.get('entity')}: no non-finite filter"
        assert drop_at > keys.index("map_y")
        if "resample" in keys:
            assert drop_at < keys.index("resample"), (
                f"{series.get('entity')}: NaN reaches resample"
            )
    assert checked >= 3


def test_wind_chart_matches_direction_by_time_not_by_index():
    """Speed and direction are separate, independently resampled entities.

    Indexing the direction series positionally (`vars.dir.ys[i]`) mislabels every
    point as soon as the two series differ in length — and a missing direction
    must not silently become 0° (due North).
    """
    card = _wind_chart_card()
    measured = next(s for s in card["entities"] if s.get("name") == "Measured")
    customdata = measured["customdata"]
    annotations = card["layout"]["annotations"]
    for code in (customdata, annotations):
        assert "vars.dir.ys[i]" not in code
        assert "dirYs[i]" not in code
        assert "dirs[i] || 0" not in code
        assert "new Date" in code and "Math.abs" in code  # timestamp matching
    assert "Number.isFinite" in annotations


def test_wind_speed_sensors_share_exact_unit_of_measurement():
    """All wind speed sensors must use `kts` so plotly-graph-card puts them on `y1`.

    If `boat_wind_speed` uses `kn` while `wind_forecast_flat` uses `kts`,
    plotly-graph-card automatically creates a second Y-axis (`yaxis2`) on the right.
    Since layout only locks `yaxis` (`fixedrange: true`), `yaxis2` remains unlocked,
    allowing the user to drag forecast data vertically relative to measured data.
    """
    derived = {s["unique_id"]: s for s in _template_sensors("derived_n2k.yaml")}
    forecast = {s["unique_id"]: s for s in _template_sensors("forecast.yaml")}
    assert derived["boat_wind_speed"]["unit_of_measurement"] == "kts"
    assert forecast["wind_forecast_flat"]["unit_of_measurement"] == "kts"
    assert derived["boat_stw"]["unit_of_measurement"] == "kts"
    assert derived["boat_sog"]["unit_of_measurement"] == "kts"


def test_build_inlines_snippets_into_filter_fn_without_the_fn_marker():
    """`filters: - fn:` is raw JS — the `$fn ` marker would break the card."""
    snippets = {"snippet": "({ ys }) => ys"}
    node = {"filters": [{"fn": "$include:snippet"}], "customdata": "$include:snippet"}
    build.resolve_includes(node, snippets)
    assert node["filters"][0]["fn"] == "({ ys }) => ys"
    assert node["customdata"] == "$fn ({ ys }) => ys"


def test_position_section_has_hdg_compass_card_before_cog():
    """Position section must feature HDG compass card (magnetic + variation) before COG card."""
    pos_file = os.path.join(SAILING_DASH_DIR, "src", "yaml", "dashboard", "sections", "02_position.yaml")
    with open(pos_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cards = data[0]["cards"]
    compass_cards = [c for c in cards if c.get("type") == "custom:compass-card"]
    assert len(compass_cards) >= 2
    assert compass_cards[0]["header"]["title"]["value"] == "HDG"
    assert compass_cards[1]["header"]["title"]["value"] == "COG"
    assert compass_cards[0]["indicator_sensors"][0]["sensor"] == "sensor.boat_heading"
    assert compass_cards[0]["value_sensors"][0]["sensor"] == "sensor.boat_heading"
    assert "sensor.boat_magnetic_variation" in compass_cards[0]["card_mod"]["style"]

    derived = {s["unique_id"]: s for s in _template_sensors("derived_n2k.yaml")}
    assert "boat_heading_magnetic" in derived
    assert "boat_magnetic_variation" in derived
    assert "boat_heading" in derived
    assert "boat_magnetic_variation" in derived["boat_heading"]["state"]
    assert "% 360" in derived["boat_heading"]["state"]

    # Ensure generic fallbacks and entity matching logic
    from helpers import map_nmea_sensors
    assert map_nmea_sensors.DEFAULT_FALLBACKS["heading"] == "sensor.vessel_heading"
    assert map_nmea_sensors.DEFAULT_FALLBACKS["variation"] == "sensor.magnetic_variation"
    sample_entities = [
        "sensor.direction_data_raymarine_display_1180407_pk_4f6c1a8dbb8120f1d9f6a64174ce2819_heading",
        "sensor.vessel_heading_raymarine_20_442559_pk_b70bbc9b5eef0afbfed7ae988ce2ddb4_heading",
        "sensor.vessel_heading_raymarine_20_442559_pk_b70bbc9b5eef0afbfed7ae988ce2ddb4_variation",
    ]
    matched = map_nmea_sensors.match_entities(sample_entities)
    assert matched["heading"] == "sensor.vessel_heading_raymarine_20_442559_pk_b70bbc9b5eef0afbfed7ae988ce2ddb4_heading"
    assert matched["variation"] == "sensor.vessel_heading_raymarine_20_442559_pk_b70bbc9b5eef0afbfed7ae988ce2ddb4_variation"


def test_wind_chart_js_snippets():
    """Run the node regression suite for src/js/common/ wind snippets."""
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        ["node", JS_SNIPPET_TEST], capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_config_yaml_parsing_and_filtering(tmp_path):
    """Test loading config.yaml, card/section filtering, and chart time window generation."""
    config_file = tmp_path / "config.yaml"
    template_file = tmp_path / "config.yaml.template"

    # Write test template
    template_file.write_text(
        "time_window:\n"
        "  history_hours: 4\n"
        "  forecast_days: 3\n"
        "sections:\n"
        "  sensors:\n"
        "    enabled: true\n"
        "    cards:\n"
        "      stw_gauge: true\n"
        "      depth_gauge: true\n"
        "      sog_gauge: true\n"
        "  waves:\n"
        "    enabled: true\n"
        "    cards:\n"
        "      glance: true\n"
        "      chart: true\n",
        encoding="utf-8",
    )

    # 1. Test baseline default load
    cfg = build.load_config(str(config_file), str(template_file))
    assert cfg["time_window"]["history_hours"] == 4
    assert cfg["time_window"]["forecast_days"] == 3

    # 2. Override with custom time window and card/section disabling
    config_file.write_text(
        "time_window:\n"
        "  history_hours: 6\n"
        "  forecast_days: 5\n"
        "sections:\n"
        "  sensors:\n"
        "    cards:\n"
        "      stw_gauge: false\n"
        "  waves:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    cfg = build.load_config(str(config_file), str(template_file))
    assert cfg["time_window"]["history_hours"] == 6
    assert cfg["time_window"]["forecast_days"] == 5
    assert cfg["sections"]["sensors"]["cards"]["stw_gauge"] is False
    assert cfg["sections"]["waves"]["enabled"] is False

    # 3. Test build_sensors with customized config
    with patch.object(build, "BUILD_DIR", str(tmp_path)):
        build.ensure_dirs()
        build.build_sensors(cfg)
        sensors_yaml = yaml.safe_load((tmp_path / "sensors-sailing.yaml").read_text(encoding="utf-8"))
        chart_tw = None
        for item in sensors_yaml.get("template", []):
            for s in item.get("sensor", []):
                if s.get("unique_id") == "chart_time_window":
                    chart_tw = s
        assert chart_tw is not None
        assert chart_tw["attributes"]["history_hours"] == "{{ 6 }}"
        assert chart_tw["attributes"]["forecast_hours"] == "{{ 120 }}"

        # Verify rest resource_template fallback int(120)
        rest_items = sensors_yaml.get("rest", [])
        assert len(rest_items) > 0
        for r in rest_items:
            assert "int(120)" in r.get("resource_template", "")

    # 4. Test build_dashboard filtering with customized config
    with patch.object(build, "BUILD_DIR", str(tmp_path)):
        build.ensure_dirs()
        build.build_dashboard(cfg)
        dash_data = yaml.safe_load((tmp_path / "dashboard-sailing.yaml").read_text(encoding="utf-8"))
        sec_list = dash_data["views"][0]["sections"]
        sec_headings = [s["cards"][0].get("heading") for s in sec_list if s.get("cards")]
        assert "Waves" not in sec_headings
        assert "Sensors" in sec_headings

        sensors_sec = next(s for s in sec_list if s["cards"][0].get("heading") == "Sensors")
        card_names = [c.get("name") for c in sensors_sec["cards"] if isinstance(c, dict)]
        assert "STW (kn)" not in card_names
        assert "Depth (m)" in card_names
        assert "SOG (kn)" in card_names

        # Verify plotly-graph cards have injected hours_to_show=126 and time_offset='120h'
        wind_sec = next(s for s in sec_list if s["cards"][0].get("heading") == "Wind Direction & Speed")
        plotly_card = next(c for c in wind_sec["cards"] if isinstance(c, dict) and c.get("type") == "custom:plotly-graph")
        assert plotly_card["hours_to_show"] == 126
        assert plotly_card["time_offset"] == "120h"

        # Ensure all entity traces on wind chart have extend_to_present: false so measured data doesn't spill past Now into tooltip
        wind_entities = plotly_card["entities"]
        measured_entities = [e for e in wind_entities if e.get("entity") in ("sensor.wind_direction_history", "sensor.boat_wind_speed")]
        assert len(measured_entities) >= 3
        for me in measured_entities:
            assert me.get("extend_to_present") is False, f"Entity {me.get('name', me.get('entity'))} missing extend_to_present: false!"

        # Ensure hoverdistance is not set to -1 in layout (which caused Plotly to match nearest history point across infinite X distance)
        layout = plotly_card.get("layout", {})
        assert layout.get("hoverdistance") != -1, "layout.hoverdistance should not be -1"

        # Ensure temporary 'id' tags are stripped
        dash_str = (tmp_path / "dashboard-sailing.yaml").read_text(encoding="utf-8")
        assert "id: stw_gauge" not in dash_str
        assert "id: depth_gauge" not in dash_str


def test_configure_py_helper(tmp_path):
    """Test helpers/configure.py in non-interactive CLI mode."""
    import configure
    config_file = tmp_path / "config.yaml"
    template_file = tmp_path / "config.yaml.template"

    template_file.write_text(
        "time_window:\n"
        "  history_hours: 4\n"
        "  forecast_days: 3\n"
        "sections:\n"
        "  sensors:\n"
        "    enabled: true\n"
        "    cards:\n"
        "      stw_gauge: true\n",
        encoding="utf-8",
    )

    test_args = [
        "configure.py",
        "--config-file", str(config_file),
        "--template-file", str(template_file),
        "--non-interactive",
        "--history-hours", "8",
        "--forecast-days", "4",
    ]
    with patch.object(sys, "argv", test_args):
        configure.main()

    assert config_file.exists()
    written_cfg = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert written_cfg["time_window"]["history_hours"] == 8
    assert written_cfg["time_window"]["forecast_days"] == 4
    assert written_cfg["sections"]["sensors"]["cards"]["stw_gauge"] is True


def test_all_dashboard_and_automation_yaml_files_use_only_virtual_sensors():
    """Ensure NO dashboard section, automation, or forecast/open-meteo sensor file
    directly references hardware/physical NMEA sensors.

    Only `derived_n2k.yaml` (generated by map_nmea_sensors.py) is allowed to contain
    references to raw physical hardware entities.
    """
    src_dir = os.path.join(SAILING_DASH_DIR, "src", "yaml")
    allowed_virtual_entities = {
        "sensor.boat_stw",
        "sensor.boat_depth",
        "sensor.boat_wind_speed",
        "sensor.boat_wind_angle",
        "sensor.boat_cog",
        "sensor.boat_sog",
        "sensor.boat_heading_magnetic",
        "sensor.boat_magnetic_variation",
        "sensor.boat_heading",
        "sensor.boat_latitude_raw",
        "sensor.boat_longitude_raw",
        "sensor.boat_pressure_raw",
        "sensor.wind_direction_history",
        "sensor.barometer_mmhg",
        "sensor.boat_latitude",
        "sensor.boat_longitude",
        "sensor.chart_time_window",
        "sensor.wind_forecast_flat",
        "sensor.wind_forecast_next_hour",
        "sensor.wind_gust_next_hour",
        "sensor.wave_forecast_flat",
        "sensor.wave_height_next_hour",
        "sensor.wave_period_next_hour",
        "sensor.wind_forecast_rest",
        "sensor.wave_forecast_rest",
    }

    yaml_files = []
    for root, _, files in os.walk(src_dir):
        for file in files:
            if file.endswith(".yaml") and file != "derived_n2k.yaml":
                yaml_files.append(os.path.join(root, file))

    assert len(yaml_files) >= 8

    for file_path in yaml_files:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        rel_path = os.path.relpath(file_path, SAILING_DASH_DIR)
        referenced_sensors = set(re.findall(r"sensor\.[a-z0-9_]+", content))
        disallowed = referenced_sensors - allowed_virtual_entities

        assert not disallowed, (
            f"Direct physical/hardware sensor references {disallowed} found in {rel_path}! "
            f"All UI cards and automations must use canonical virtual sensors (sensor.boat_*)."
        )


def _build_with_chart_config(tmp_path, extra_top_yaml=""):
    """Build the dashboard into tmp_path with a minimal chart config."""
    template_file = tmp_path / "config.yaml.template"
    config_file = tmp_path / "config.yaml"

    template_file.write_text(
        extra_top_yaml
        + "time_window:\n"
        "  history_hours: 6\n"
        "  forecast_days: 5\n"
        "sections:\n"
        "  wind:\n"
        "    enabled: true\n"
        "  waves:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    with patch.object(build, "BUILD_DIR", str(tmp_path)), patch.object(build, "DEFAULT_TEMPLATE_PATH", str(template_file)), patch.object(build, "DEFAULT_CONFIG_PATH", str(config_file)):
        build.ensure_dirs()
        build.build_cards()
        build.build_sensors()
        build.build_dashboard()

    return (tmp_path / "dashboard-sailing.yaml").read_text(encoding="utf-8")


def _plotly_chart_cards(dash_str):
    """Return every `custom:plotly-graph` card of the built dashboard."""
    dash = yaml.safe_load(dash_str)
    return [
        card
        for section in dash["views"][0]["sections"]
        for card in section.get("cards", [])
        if isinstance(card, dict) and card.get("type") == "custom:plotly-graph"
    ]


def test_chart_style_is_global_and_applies_to_wind_and_waves(tmp_path):
    """`chart_style` is a top-level option shared by every chart.

    Both styles render the same `custom:plotly-graph` card: only the arrow
    annotation layout differs, injected as `arrow_layout` and read at runtime
    by the single shared src/js/common/plotly_chart_annotations.js layer. The
    retired ApexCharts / hand-rolled SVG implementations must not come back.
    """
    for style, expected_layout in (("open_meteo", "top_row"), ("plotly", "on_point")):
        dash_str = _build_with_chart_config(tmp_path, f"chart_style: {style}\n")

        assert "type: custom:apexcharts-card" not in dash_str
        assert "type: custom:openmeteo-wind-card" not in dash_str

        kinds = {}
        for card in _plotly_chart_cards(dash_str):
            if "arrow_kind" not in card:
                continue
            kinds[card["arrow_kind"]] = card
            assert card["arrow_layout"] == expected_layout
            # Time window still comes from config.yaml: 6h history + 5d forecast.
            assert card["hours_to_show"] == 126
            assert card["time_offset"] == "120h"

        # The very same style reached the wind AND the wave chart.
        assert set(kinds) == {"wind", "wave"}


def test_chart_style_defaults_to_open_meteo(tmp_path):
    """With no explicit style the arrows must form the top row (open_meteo)."""
    cards = _plotly_chart_cards(_build_with_chart_config(tmp_path))
    arrow_cards = [c for c in cards if "arrow_kind" in c]
    assert arrow_cards
    for card in arrow_cards:
        assert card["arrow_layout"] == "top_row"
        assert card["arrow_spacing_hours"] == 3


def test_unknown_chart_style_falls_back_to_the_default(tmp_path):
    """A typo in config.yaml must not break the build."""
    assert build.resolve_chart_style({"chart_style": "plotly"}) == ("plotly", "on_point")
    assert build.resolve_chart_style({"chart_style": "nonsense"}) == ("open_meteo", "top_row")
    assert build.resolve_chart_style({}) == ("open_meteo", "top_row")


def test_arrow_spacing_hours_is_global_and_reaches_every_chart(tmp_path):
    """The top-level `arrow_spacing_hours` must reach wind and wave charts."""
    cards = _plotly_chart_cards(_build_with_chart_config(tmp_path, "arrow_spacing_hours: 6\n"))
    arrow_cards = [c for c in cards if "arrow_kind" in c]
    assert {c["arrow_kind"] for c in arrow_cards} == {"wind", "wave"}
    assert all(c["arrow_spacing_hours"] == 6 for c in arrow_cards)


def test_arrow_length_scale_is_global_with_default_of_three(tmp_path):
    """The arrow length amplifier defaults to 3 and reaches every chart."""
    default_cards = [c for c in _plotly_chart_cards(_build_with_chart_config(tmp_path)) if "arrow_kind" in c]
    assert default_cards
    assert all(c["arrow_length_scale"] == 3 for c in default_cards)

    scaled = [
        c
        for c in _plotly_chart_cards(_build_with_chart_config(tmp_path, "arrow_length_scale: 8\n"))
        if "arrow_kind" in c
    ]
    assert {c["arrow_kind"] for c in scaled} == {"wind", "wave"}
    assert all(c["arrow_length_scale"] == 8 for c in scaled)


def test_measured_arrows_on_line_is_global_with_default_true(tmp_path):
    """Measured arrows are anchored on the measured line unless disabled."""
    default_cards = [c for c in _plotly_chart_cards(_build_with_chart_config(tmp_path)) if "arrow_kind" in c]
    assert default_cards
    assert all(c["measured_arrows_on_line"] is True for c in default_cards)

    disabled = [
        c
        for c in _plotly_chart_cards(_build_with_chart_config(tmp_path, "measured_arrows_on_line: false\n"))
        if "arrow_kind" in c
    ]
    assert {c["arrow_kind"] for c in disabled} == {"wind", "wave"}
    assert all(c["measured_arrows_on_line"] is False for c in disabled)


def test_global_chart_options_from_user_config_are_applied(tmp_path):
    """Top-level chart options in config.yaml must override the template."""
    template_file = tmp_path / "config.yaml.template"
    config_file = tmp_path / "config.yaml"
    template_file.write_text(
        "chart_style: open_meteo\n"
        "arrow_spacing_hours: 3\n"
        "sections:\n"
        "  wind:\n"
        "    enabled: true\n"
        "    cards:\n"
        "      glance: true\n"
        "      chart: true\n",
        encoding="utf-8",
    )
    config_file.write_text(
        "chart_style: plotly\n"
        "arrow_spacing_hours: 6\n"
        "arrow_length_scale: 5\n"
        "sections:\n"
        "  wind:\n"
        "    cards:\n"
        "      glance: false\n",
        encoding="utf-8",
    )

    config = build.load_config(str(config_file), str(template_file))
    assert config["chart_style"] == "plotly"
    assert config["arrow_spacing_hours"] == 6
    assert config["arrow_length_scale"] == 5
    # `cards` stays a per-key merge, so untouched toggles survive.
    assert config["sections"]["wind"]["cards"] == {"glance": False, "chart": True}


def _glance_cards(dash_str):
    """Return every `glance` value-tile card of the built dashboard."""
    dash = yaml.safe_load(dash_str)
    return [
        card
        for section in dash["views"][0]["sections"]
        for card in section.get("cards", [])
        if isinstance(card, dict) and card.get("type") == "glance"
    ]


def test_forecast_style_is_global_with_markers_as_default(tmp_path):
    """`forecast_style` picks the look of the forecast series on every chart."""
    assert build.resolve_forecast_style({})[0] == "markers"
    # A typo must fall back instead of breaking the build.
    assert build.resolve_forecast_style({"forecast_style": "nonsense"})[0] == "markers"

    default_series = [
        s
        for card in _plotly_chart_cards(_build_with_chart_config(tmp_path))
        for s in card.get("entities", [])
        if s.get("name") in build.FORECAST_SERIES_NAMES
    ]
    assert default_series
    assert all(s["mode"] == "markers" and s["marker"]["symbol"] == "diamond" for s in default_series)

    for style, mode, shape_key, shape in (
        ("circle", "markers", "symbol", "circle"),
        ("line", "lines", "dash", "solid"),
        ("dot", "lines", "dash", "dot"),
    ):
        series = [
            s
            for card in _plotly_chart_cards(_build_with_chart_config(tmp_path, f"forecast_style: {style}\n"))
            for s in card.get("entities", [])
            if s.get("name") in build.FORECAST_SERIES_NAMES
        ]
        # Both the wind and the wave forecast follow the very same style.
        assert len(series) == 2, style
        for s in series:
            assert s["mode"] == mode
            assert s["marker" if mode == "markers" else "line"][shape_key] == shape


def test_series_colors_come_from_config_and_reach_tiles_too(tmp_path):
    """The `colors` block recolours the traces AND their glance tiles."""
    extra = (
        "colors:\n"
        "  measured: '#112233'\n"
        "  measured_gusts: '#223344'\n"
        "  forecast: '#334455'\n"
        "  forecast_gusts: '#445566'\n"
    )
    dash_str = _build_with_chart_config(tmp_path, extra)

    colored = {}
    for card in _plotly_chart_cards(dash_str):
        for series in card.get("entities", []):
            name = series.get("name")
            if name not in build.SERIES_COLOR_ROLES:
                continue
            spec = series.get("marker") or series.get("line") or {}
            colored[name] = spec.get("color")
    assert colored == {
        "Measured": "#112233",
        "Gusts (measured)": "#223344",
        "Forecast": "#334455",
        "Gusts (forecast)": "#445566",
    }

    # The value tiles are styled by card_mod strings; they must not drift away
    # from the trace colour they belong to.
    tile_colors = {
        tile["name"]: tile["card_mod"]["style"]
        for card in _glance_cards(dash_str)
        for tile in card.get("entities", [])
        if tile.get("name") in build.TILE_COLOR_ROLES
    }
    assert "color: #112233 !important" in tile_colors["Measured now"]
    assert "color: #334455 !important" in tile_colors["Forecast next 1h"]
    assert "color: #445566 !important" in tile_colors["Gusts next 1h"]


def test_now_label_and_forecast_history_arrow_opacity_are_injected(tmp_path):
    """Both opacity options are global, with their documented defaults."""
    default_cards = [c for c in _plotly_chart_cards(_build_with_chart_config(tmp_path)) if "arrow_kind" in c]
    assert default_cards
    assert all(c["now_label_opacity"] == 0.55 for c in default_cards)
    assert all(c["forecast_history_arrow_opacity"] == 0.4 for c in default_cards)

    tuned = [
        c
        for c in _plotly_chart_cards(
            _build_with_chart_config(
                tmp_path, "now_label_opacity: 0.2\nforecast_history_arrow_opacity: 0\n"
            )
        )
        if "arrow_kind" in c
    ]
    assert {c["arrow_kind"] for c in tuned} == {"wind", "wave"}
    assert all(c["now_label_opacity"] == 0.2 for c in tuned)
    assert all(c["forecast_history_arrow_opacity"] == 0 for c in tuned)
