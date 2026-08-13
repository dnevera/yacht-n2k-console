#!/usr/bin/env python3
"""
AIS Dashboard Build Script.

Compiles src/yaml/dashboard/** (plus config.yaml/config.yaml.template
toggles) into build/dashboard-ais.yaml — a much smaller equivalent of
ha/sailing-dash/helpers/build.py, following the same load_config() /
section-merge conventions so the two packages stay easy to read side by
side.
"""

import os

import yaml

# This script lives in ha/ais/helpers/; every path below is relative to the
# subproject root one level up (src/, build/, .env stay there).
HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HELPERS_DIR)
SRC_DIR = os.path.join(ROOT_DIR, "src")
BUILD_DIR = os.path.join(ROOT_DIR, "build")
DEFAULT_CONFIG_PATH = os.path.join(ROOT_DIR, "config.yaml")
DEFAULT_TEMPLATE_PATH = os.path.join(ROOT_DIR, "config.yaml.template")

# Human-readable label for each attribute the ais_targets integration may
# expose (see custom_components/ais_targets/README.md) — used to build the
# markdown detail-list card's table header/columns.
FIELD_LABELS = {
    "mmsi": "MMSI",
    "vessel_name": "Name",
    "callsign": "Callsign",
    "ship_type": "Type",
    "length": "Length (m)",
    "beam": "Beam (m)",
    "destination": "Destination",
    "eta": "ETA",
    "sog": "SOG (kn)",
    "cog": "COG (°)",
    "heading": "Heading (°)",
    "nav_status": "Nav Status",
    "rate_of_turn": "Rate of Turn",
}
DEFAULT_DETAIL_FIELDS = [
    "mmsi",
    "vessel_name",
    "callsign",
    "ship_type",
    "length",
    "beam",
    "destination",
]

# The `geo_location` `source` reported by every ais_targets entity — must
# match GEO_LOCATION_SOURCE in custom_components/ais_targets/const.py and the
# `geo_location_sources:` list on the map card.
GEO_LOCATION_SOURCE = "ais_targets"


def load_config(config_path=None, template_path=None):
    """Load configuration from config.yaml, merging missing keys from
    config.yaml.template — same fallback convention as ha/sailing-dash."""
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
        if isinstance(override, dict):
            if isinstance(override.get("map"), dict):
                config.setdefault("map", {}).update(override["map"])
            if "target_stale_timeout_minutes" in override:
                config["target_stale_timeout_minutes"] = override[
                    "target_stale_timeout_minutes"
                ]
            if isinstance(override.get("detail_fields"), list):
                config["detail_fields"] = override["detail_fields"]

    return config


def build_detail_list_content(detail_fields):
    """Return the Jinja markdown-card `content` string listing every live
    `geo_location.ais_*` entity, one row per vessel, columns per
    `detail_fields` (falls back to DEFAULT_DETAIL_FIELDS on an empty/unknown
    list). Evaluated by Home Assistant itself — no extra card dependency."""
    fields = [f for f in detail_fields if f in FIELD_LABELS] or list(
        DEFAULT_DETAIL_FIELDS
    )

    header = " | ".join(FIELD_LABELS[f] for f in fields)
    separator = " | ".join("---" for _ in fields)
    row_cells = " | ".join(
        "{{ t.attributes.%s if t.attributes.%s is not none else '—' }}" % (f, f)
        for f in fields
    )

    return (
        "{% set targets = states.geo_location "
        "| selectattr('attributes.source', 'defined') "
        f"| selectattr('attributes.source', 'eq', '{GEO_LOCATION_SOURCE}') "
        "| list %}\n"
        "{% if targets | length == 0 %}\n"
        "_No AIS targets currently in range._\n"
        "{% else %}\n"
        f"| {header} |\n"
        f"| {separator} |\n"
        "{% for t in targets %}\n"
        f"| {row_cells} |\n"
        "{% endfor %}\n"
        "{% endif %}"
    )


def ensure_dirs():
    os.makedirs(BUILD_DIR, exist_ok=True)


def build_dashboard(config=None):
    """Build build/dashboard-ais.yaml from header.yaml + sections/*.yaml."""
    if config is None:
        config = load_config()

    header_path = os.path.join(SRC_DIR, "yaml", "dashboard", "header.yaml")
    sections_dir = os.path.join(SRC_DIR, "yaml", "dashboard", "sections")

    with open(header_path, "r", encoding="utf-8") as f:
        header_data = yaml.safe_load(f)

    map_cfg = config.get("map", {}) if isinstance(config.get("map"), dict) else {}
    default_zoom = int(map_cfg.get("default_zoom", 12))
    aspect_ratio = str(map_cfg.get("aspect_ratio", "16x9"))
    detail_fields = config.get("detail_fields") or list(DEFAULT_DETAIL_FIELDS)

    sections = []
    for fname in sorted(os.listdir(sections_dir)):
        if not fname.endswith(".yaml"):
            continue
        fpath = os.path.join(sections_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            sec_data = yaml.safe_load(f)

        sec_items = sec_data if isinstance(sec_data, list) else [sec_data]

        for item in sec_items:
            if not isinstance(item, dict) or not isinstance(item.get("cards"), list):
                continue
            for card in item["cards"]:
                if not isinstance(card, dict):
                    continue
                card_id = card.pop("id", None)
                if card_id == "map" and card.get("type") == "map":
                    card["default_zoom"] = default_zoom
                    card["aspect_ratio"] = aspect_ratio
                elif card_id == "detail_list" and card.get("type") == "markdown":
                    card["content"] = build_detail_list_content(detail_fields)

        sections.extend(sec_items)

    if header_data and "views" in header_data and len(header_data["views"]) > 0:
        header_data["views"][0]["sections"] = sections

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


if __name__ == "__main__":
    main()
