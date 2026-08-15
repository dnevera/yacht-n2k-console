"""Regression tests for the AIS dashboard template (ha/ais/src/yaml/**).

Both cases here were real defects on the phone:
  * the target-list overlay spans the whole map height, so its empty part below
    the last row swallowed every tap/drag — the map was dead where it was
    plainly visible;
  * a two-finger up/down drag arrives as a `wheel` event and made Leaflet zoom
    the map on its own (see ha/ais/src/js/ais-select-bridge.js).
"""
import os
import re
import shutil
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AIS_DIR = os.path.join(PROJECT_ROOT, "ha", "ais")
MAP_SECTION = os.path.join(AIS_DIR, "src", "yaml", "dashboard", "sections", "01_ais_map.yaml")
BRIDGE_JS = os.path.join(AIS_DIR, "src", "js", "ais-select-bridge.js")
JS_TEST = os.path.join(PROJECT_ROOT, "tests", "js", "ais_select_bridge.test.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_table_overlays_let_taps_through_to_the_map():
    """Only the painted card takes events; the rest of the overlay does not."""
    text = _read(MAP_SECTION)
    # Both table overlays (collapsed + expanded) plus the detail card.
    assert text.count("pointer-events: none;") >= 3
    assert text.count("pointer-events: auto;") >= 3


def test_map_wheel_is_kept_away_from_leaflet():
    js = _read(BRIDGE_JS)
    assert "leaflet-container" in js
    assert "stopImmediatePropagation" in js
    # No preventDefault in the wheel gate: the dashboard must keep scrolling.
    wheel_gate = js.split("MAP ZOOM GESTURES", 1)[1].split("*/", 1)[1]
    assert "preventDefault" not in wheel_gate


def test_own_boat_is_marked_on_the_map_without_duplicating_the_mmsi():
    """⛵ in the marker label + a ring drawn from the `is_own_ship` attribute.

    The MMSI is owned by the `ais_targets` config entry, so neither the
    dashboard template nor the bridge may hardcode it.
    """
    geo = _read(
        os.path.join(AIS_DIR, "custom_components", "ais_targets", "geo_location.py")
    )
    label = geo.split("def _map_label", 1)[1].split("@property", 1)[0]
    assert "_OWN_BOAT_ICON" in label

    js = _read(BRIDGE_JS)
    assert "is_own_ship" in js
    assert "ais-own-ship" in js
    # No literal MMSI anywhere in the frontend assets.
    assert not re.search(r"geo_location\.ais_\d", js)
    assert not re.search(r"geo_location\.ais_\d", _read(MAP_SECTION))


def test_ais_select_bridge_js_suite():
    """Run the node regression suite for the AIS bridge."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        ["node", JS_TEST], capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    assert result.returncode == 0, result.stdout + result.stderr
