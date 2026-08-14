#!/usr/bin/env python3
"""
AIS Dashboard Build Script.

Compiles src/yaml/dashboard/** (plus config.yaml/config.yaml.template
toggles) into build/dashboard-ais.yaml — a much smaller equivalent of
ha/sailing-dash/helpers/build.py, following the same load_config() /
section-merge conventions so the two packages stay easy to read side by
side.

The detail list is a `custom:auto-entities` card wrapping a
`custom:flex-table-card`: auto-entities discovers the changing set of
`geo_location.ais_*` entities at runtime, flex-table-card renders them as a
full multi-column, clickable table. This script injects the table columns
from config.yaml's `detail_fields`, and the map's zoom/height from config.yaml.

NOTE: `own_mmsi` lives ONLY in the ais_targets integration's config entry
(the single source of truth) — it is deliberately NOT read here, so the two
never drift out of sync.
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

# Human-readable label for each attribute the ais_targets integration exposes
# (see custom_components/ais_targets/geo_location.py) — used as flex-table-card
# column headers. `data` is the plain geo_location attribute key (flat, NOT `attributes.x`).
FIELD_LABELS = {
    "friendly_name": "Vessel",
    "mmsi": "MMSI",
    "vessel_name": "Name",
    "callsign": "Callsign",
    "ship_type": "Type",
    "length": "Length (m)",
    "beam": "Beam (m)",
    "name": "Vessel",
    "state": "Dist (km)",
    "destination": "Destination",
    "eta": "ETA",
    "sog": "SOG (kn)",
    "cog": "COG (°)",
    "heading": "Heading (°)",
    "nav_status": "Nav Status",
    "rate_of_turn": "Rate of Turn",
    "last_seen": "Updated",
}
# Right-aligned numeric columns (purely cosmetic).
RIGHT_ALIGNED_FIELDS = {"state", "length", "beam", "sog", "cog", "heading"}
DEFAULT_DETAIL_FIELDS = [
    "mmsi",
    "vessel_name",
    "callsign",
    "ship_type",
    "length",
    "beam",
    "destination",
]
# Always appended (in this order) after the configured identity/voyage columns
# — exactly the live nav attributes every target exposes, so the table never
# silently drops them regardless of `detail_fields`.
ALWAYS_TRAILING_FIELDS = ["sog", "cog", "heading", "nav_status", "last_seen"]


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


def build_columns(detail_fields):
    """Return the flex-table-card `columns` list: a 'Vessel' name column, then
    every configured `detail_fields` entry, then the always-on nav columns —
    de-duplicated while preserving order.

    IMPORTANT: flex-table-card's `data` is a FLAT key, not a JS path — it
    resolves `name`/`object_id`/`state`/... specially, then a direct member of
    the row entity, then falls back to `entity.attributes[key]` (see
    flex-table-card.js `col_type == "auto"`). A nested path like
    `attributes.mmsi` therefore never resolves and renders as `undefined`;
    worse, a comma inside `data` means "multi source", so a two-field column
    rendered as `undefinedundefined`. Hence: plain attribute keys, and the
    special `name` key for the vessel column."""
    ordered = ["name", "state"]
    for field in list(detail_fields) + ALWAYS_TRAILING_FIELDS:
        if field not in ordered:
            ordered.append(field)
    columns = []
    for field in ordered:
        col = {"name": FIELD_LABELS.get(field, field), "data": field}
        if field in RIGHT_ALIGNED_FIELDS:
            col["align"] = "right"
        columns.append(col)
    return columns


def ensure_dirs():
    os.makedirs(BUILD_DIR, exist_ok=True)


def overlay_style(height_px):
    """card_mod style pinning the target list as an overlay over the map's
    right-hand side. The two overlay cards are the 2nd/3rd children of the
    outer vertical-stack (collapsed side-bar / expanded table); only one of
    them is ever visible, since each is wrapped in a `conditional`."""
    return (
        "#root { position: relative; }\n"
        "#root > *:nth-child(2),\n"
        "#root > *:nth-child(3) {\n"
        "  position: absolute;\n"
        "  top: 12px;\n"
        "  right: 12px;\n"
        "  z-index: 1;\n"
        f"  max-height: {max(height_px - 24, 120)}px;\n"
        "  overflow: auto;\n"
        "}\n"
    )


def _patch_cards(cards, default_zoom, height_px, detail_fields, depth=0):
    """Recursively walk a (possibly nested, e.g. vertical-stack) cards list and
    patch the `id: map` / `id: detail_list` cards wherever they live, plus the
    overlay geometry on the outermost vertical-stack."""
    if not isinstance(cards, list):
        return
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_id = card.pop("id", None)
        if card_id == "map" and card.get("type") == "map":
            card["default_zoom"] = default_zoom
            card["card_mod"] = {
                "style": (
                    f"ha-card {{ height: {height_px}px; }}\n"
                    f"#map {{ height: {height_px}px !important; }}\n"
                )
            }
        elif card_id == "detail_list" and card.get("type") == "custom:flex-table-card":
            card["columns"] = build_columns(detail_fields)
        elif depth == 0 and card.get("type") == "vertical-stack":
            card["card_mod"] = {"style": overlay_style(height_px)}
        # Recurse into any nested card containers (vertical-stack, grid, ...).
        if isinstance(card.get("cards"), list):
            _patch_cards(
                card["cards"], default_zoom, height_px, detail_fields, depth + 1
            )
        if isinstance(card.get("card"), dict):
            _patch_cards(
                [card["card"]], default_zoom, height_px, detail_fields, depth + 1
            )


def build_dashboard(config=None):
    """Build build/dashboard-ais.yaml from header.yaml + sections/*.yaml.

    The view is a `panel` view (the only Lovelace view type that renders
    truly edge-to-edge, without a centered max-width column like
    `sections`/`masonry`), so it holds exactly ONE top-level card per file in
    sections/*.yaml (typically a `vertical-stack` wrapping heading/map/table)."""
    if config is None:
        config = load_config()

    header_path = os.path.join(SRC_DIR, "yaml", "dashboard", "header.yaml")
    sections_dir = os.path.join(SRC_DIR, "yaml", "dashboard", "sections")

    with open(header_path, "r", encoding="utf-8") as f:
        header_data = yaml.safe_load(f)

    map_cfg = config.get("map", {}) if isinstance(config.get("map"), dict) else {}
    default_zoom = int(map_cfg.get("default_zoom", 12))
    height_px = int(map_cfg.get("height_px", 480))
    detail_fields = config.get("detail_fields") or list(DEFAULT_DETAIL_FIELDS)

    cards = []
    for fname in sorted(os.listdir(sections_dir)):
        if not fname.endswith(".yaml"):
            continue
        fpath = os.path.join(sections_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            sec_data = yaml.safe_load(f)

        sec_items = sec_data if isinstance(sec_data, list) else [sec_data]
        _patch_cards(sec_items, default_zoom, height_px, detail_fields)
        cards.extend(sec_items)

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


if __name__ == "__main__":
    main()
