#!/usr/bin/env python3
"""
AIS Dashboard Build Script.

Assembles src/yaml/dashboard/header.yaml + src/yaml/dashboard/sections/*.yaml
into build/dashboard-ais.yaml.

SINGLE SOURCE OF TRUTH = THE TEMPLATES.
The templates own the dashboard's structure and styling completely. This script
does exactly two things:

  1. substitutes the explicit `${AIS_*}` placeholders the templates declare,
     using values from config.yaml (config.yaml.template holds the defaults);
  2. concatenates the section files into the view's `cards` list.

It must NEVER rewrite, inject or "normalise" anything inside a card. An earlier
revision generated whole card_mod blocks and the table's `columns` list from
config.yaml and silently overwrote whatever was in the templates, so hand edits
to src/yaml/** disappeared on the next deploy ("edited locally, deployed, still
the old card on stage"). Do not reintroduce that: to change the look, edit the
template; to make something configurable, add a placeholder to the template AND
a key here.

NOTE: runtime settings (gateway host/port, `own_mmsi`, update/stale intervals)
live ONLY in the ais_targets integration's config entry, not here.
"""

import os
import re
import string
import sys

import yaml

# This script lives in ha/ais/helpers/; every path below is relative to the
# subproject root one level up (src/, build/, .env stay there).
HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HELPERS_DIR)
SRC_DIR = os.path.join(ROOT_DIR, "src")
BUILD_DIR = os.path.join(ROOT_DIR, "build")
DEFAULT_CONFIG_PATH = os.path.join(ROOT_DIR, "config.yaml")
DEFAULT_TEMPLATE_PATH = os.path.join(ROOT_DIR, "config.yaml.template")

PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def load_config(config_path=None, template_path=None):
    """Load config.yaml.template (defaults), then overlay config.yaml — the
    same fallback convention as ha/sailing-dash."""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if template_path is None:
        template_path = DEFAULT_TEMPLATE_PATH

    config = {}
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value

    return config


def build_placeholders(config):
    """Map every `${AIS_*}` placeholder the templates may use to its value.

    Keep this list tiny and explicit: a placeholder is a deliberate contract
    between config.yaml and a template, not a generic templating engine."""
    map_cfg = config.get("map") or {}
    tbl_cfg = config.get("table") or {}

    row_height = int(tbl_cfg.get("row_height_px", 34))
    visible_rows = int(tbl_cfg.get("visible_rows", 10))
    collapsed_rows = int(tbl_cfg.get("collapsed_visible_rows", 8))

    return {
        "AIS_MAP_ZOOM": str(int(map_cfg.get("default_zoom", 12))),
        "AIS_MAP_HEIGHT": str(map_cfg.get("height", "calc(100vh - 104px)")),
        "AIS_MARKER_SIZE": str(map_cfg.get("marker_size", "52px")),
        "AIS_MARKER_FONT_SIZE": str(map_cfg.get("marker_font_size", "10px")),
        "AIS_SORT_BY": str(tbl_cfg.get("default_sort", "state+")),
        # The table body is its own scroll container: N rows visible, the rest
        # reachable by scrolling (flex-table-card's `max_rows` would truncate).
        # Below this width the wide table stops squeezing and scrolls
        # horizontally instead of clipping its (nowrap) cells.
        "AIS_TABLE_MIN_WIDTH": str(int(tbl_cfg.get("min_width_px", 1100))),
        # Overlay widths are plain CSS lengths, set exactly like the map's
        # height (e.g. `calc(60vw - 50px)`), not px numbers.
        "AIS_TABLE_WIDTH": str(tbl_cfg.get("width", "calc(100vw - 48px)")),
        "AIS_TABLE_COLLAPSED_WIDTH": str(tbl_cfg.get("collapsed_width", "300px")),
        # `table-layout: fixed` would split the width evenly and clip long
        # vessel names, so the name column keeps its own width.
        "AIS_VESSEL_COL_WIDTH": str(tbl_cfg.get("vessel_column_width", "220px")),
        "AIS_ROWS_SCROLL_PX": str(visible_rows * row_height),
        "AIS_ROWS_SCROLL_COMPACT_PX": str(collapsed_rows * row_height),
    }


def render(text, placeholders, source):
    """Substitute `${AIS_*}` placeholders, failing loudly on an unknown one."""
    unknown = {
        name for name in PLACEHOLDER_RE.findall(text) if name not in placeholders
    }
    if unknown:
        raise SystemExit(
            f"{source}: unknown placeholder(s) {sorted(unknown)} — add them to "
            "build_placeholders() (and document them in config.yaml.template)."
        )
    return string.Template(text).safe_substitute(placeholders)


def ensure_dirs():
    os.makedirs(BUILD_DIR, exist_ok=True)


def load_yaml(path, placeholders):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return yaml.safe_load(render(text, placeholders, os.path.basename(path)))


def build_dashboard(config=None):
    """Build build/dashboard-ais.yaml from header.yaml + sections/*.yaml.

    The view is a `panel` view (the only Lovelace view type that renders truly
    edge-to-edge, without a centered max-width column like `sections`/
    `masonry`), so each sections/*.yaml holds exactly ONE top-level card
    (a `custom:mod-card` wrapping the map and the overlay tables)."""
    if config is None:
        config = load_config()
    placeholders = build_placeholders(config)

    header_path = os.path.join(SRC_DIR, "yaml", "dashboard", "header.yaml")
    sections_dir = os.path.join(SRC_DIR, "yaml", "dashboard", "sections")

    header_data = load_yaml(header_path, placeholders)

    cards = []
    for fname in sorted(os.listdir(sections_dir)):
        if not fname.endswith(".yaml"):
            continue
        sec_data = load_yaml(os.path.join(sections_dir, fname), placeholders)
        if sec_data is None:
            continue
        cards.extend(sec_data if isinstance(sec_data, list) else [sec_data])

    if header_data and "views" in header_data and len(header_data["views"]) > 0:
        header_data["views"][0]["cards"] = cards

    dst_path = os.path.join(BUILD_DIR, "dashboard-ais.yaml")
    with open(dst_path, "w", encoding="utf-8") as f:
        yaml.dump(header_data, f, sort_keys=False, allow_unicode=True, width=1000)
    print(f"Built {dst_path}")


def main():
    print("Starting ais-dash build...")
    config = load_config()
    ensure_dirs()
    build_dashboard(config)
    print("Build completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
