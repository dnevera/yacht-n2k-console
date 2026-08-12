#!/usr/bin/env python3
"""
Sailing Dashboard Build Script.

Compiles modular YAML files and JS components from src/ into build/ artifacts,
and generates build/local-preview/card-configs.js for offline testing.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import yaml

# This script lives in ha/sailing-dash/helpers/; every path below is relative to
# the subproject root one level up (src/, build/, deps.yaml, .env stay there).
HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HELPERS_DIR)
SRC_DIR = os.path.join(ROOT_DIR, "src")
BUILD_DIR = os.path.join(ROOT_DIR, "build")
COMMON_JS_DIR = os.path.join(SRC_DIR, "js", "common")
DEFAULT_CONFIG_PATH = os.path.join(ROOT_DIR, "config.yaml")
DEFAULT_TEMPLATE_PATH = os.path.join(ROOT_DIR, "config.yaml.template")

# Prefix used by `$fn` scalars in dashboard YAML (evaluated by plotly-graph /
# ha-yaml-templating at runtime).
FN_PREFIX = "$fn "
# Prefix used by scalar placeholders in dashboard YAML to reference a shared
# JS snippet from src/js/common/ instead of duplicating it inline.
INCLUDE_PREFIX = "$include:"
# YAML keys whose value is raw JS evaluated by the card itself — an included
# snippet must NOT be wrapped into `$fn ...` there (plotly-graph's `filters`
# steps are the only such place today).
RAW_INCLUDE_KEYS = {"fn", "custom"}


def load_config(config_path=None, template_path=None):
    """Load configuration from config.yaml, merging missing keys from config.yaml.template."""
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
                for opt_key in (
                    "chart_style",
                    "forecast_style",
                    "arrow_spacing_hours",
                    "arrow_length_scale",
                    "measured_arrows_on_line",
                    "now_label_opacity",
                    "forecast_history_arrow_opacity",
                ):
                    if opt_key in override:
                        config[opt_key] = override[opt_key]
                if "colors" in override and isinstance(override["colors"], dict):
                    config.setdefault("colors", {}).update(override["colors"])
                if "time_window" in override and isinstance(override["time_window"], dict):
                    config.setdefault("time_window", {}).update(override["time_window"])
                if "sections" in override and isinstance(override["sections"], dict):
                    config.setdefault("sections", {})
                    for sec_key, sec_val in override["sections"].items():
                        if isinstance(sec_val, dict):
                            target_sec = config["sections"].setdefault(sec_key, {})
                            # Any scalar section option (enabled, ...)
                            # overrides the template as-is; `cards` is merged
                            # key-by-key so a partial config.yaml keeps the
                            # template's other toggles.
                            for opt_key, opt_val in sec_val.items():
                                if opt_key == "cards":
                                    continue
                                target_sec[opt_key] = opt_val
                            if "cards" in sec_val and isinstance(sec_val["cards"], dict):
                                target_sec.setdefault("cards", {}).update(sec_val["cards"])

    return config


# Chart styles. Every chart is rendered by the same `custom:plotly-graph` card —
# the only difference is where the direction arrow annotations are drawn, which
# `src/js/common/plotly_chart_annotations.js` decides from the injected
# `arrow_layout` option:
#   plotly     -> on_point: an arrow sits on each data point of the value line.
#   open_meteo -> top_row : arrows line up in one row under the chart's top
#                 edge (open-meteo.com preview style), values stay as lines.
# The option is global: it applies to the wind and the wave chart alike.
CHART_STYLES = {
    "plotly": "on_point",
    "open_meteo": "top_row",
}
DEFAULT_CHART_STYLE = "open_meteo"
# Amplifier of the arrow shaft length: the shaft grows with the value (wind
# speed in kt, wave height in m) multiplied by this factor. 1 = the old,
# barely visible growth; higher = more pronounced.
DEFAULT_ARROW_LENGTH_SCALE = 3
# Whether the measured (recorder history) arrows are anchored on the measured
# value line instead of the top row, so the direction of the measured wind is
# unambiguous in the history zone of the chart.
DEFAULT_MEASURED_ARROWS_ON_LINE = True
# Opacity of the "Now" label drawn on top of the dashed now line: the fully
# opaque white badge used to hide the traces running underneath it.
DEFAULT_NOW_LABEL_OPACITY = 0.55
# Opacity of the FORECAST direction arrows that fall left of "Now" (i.e. the
# open-meteo history overlapping the measured zone) so they read as background
# information next to the measured arrows. 0 hides them completely.
DEFAULT_FORECAST_HISTORY_ARROW_OPACITY = 0.4

# How the forecast value series is drawn. Global, applied to every chart so the
# wind and the wave forecast can never drift apart visually.
FORECAST_STYLES = {
    "markers": {"mode": "markers", "symbol": "diamond"},
    "circle": {"mode": "markers", "symbol": "circle"},
    "line": {"mode": "lines", "dash": "solid"},
    "dot": {"mode": "lines", "dash": "dot"},
}
DEFAULT_FORECAST_STYLE = "markers"

# Colours of the chart series, overridable by the `colors` block of
# config.yaml. The role of a series/tile is taken from its name, so a colour is
# defined in exactly one place instead of being repeated per trace and per
# `card_mod` style.
DEFAULT_COLORS = {
    "measured": "#4fc3f7",
    "measured_gusts": "#b0bec5",
    "forecast": "#ff7043",
    "forecast_gusts": "#78909c",
}
SERIES_COLOR_ROLES = {
    "Measured": "measured",
    "Gusts (measured)": "measured_gusts",
    "Forecast": "forecast",
    "Gusts (forecast)": "forecast_gusts",
}
TILE_COLOR_ROLES = {
    "Measured now": "measured",
    "Forecast next 1h": "forecast",
    "Gusts next 1h": "forecast_gusts",
}
# Series whose look is driven by `forecast_style`.
FORECAST_SERIES_NAMES = {"Forecast", "Wave height (forecast)"}
# Which flavour of arrows a section's chart draws (series, colour scale and
# shaft length), passed to the shared annotation layer as `arrow_kind`.
SECTION_ARROW_KINDS = {
    "wind": "wind",
    "waves": "wave",
}


def resolve_chart_style(config):
    """Return `(style, arrow_layout)` for the global `chart_style` option."""
    raw = config.get("chart_style")
    style = str(raw if raw is not None else DEFAULT_CHART_STYLE).strip().lower()
    if style not in CHART_STYLES:
        style = DEFAULT_CHART_STYLE
    return style, CHART_STYLES[style]


def resolve_forecast_style(config):
    """Return `(name, spec)` for the global `forecast_style` option."""
    raw = config.get("forecast_style")
    style = str(raw if raw is not None else DEFAULT_FORECAST_STYLE).strip().lower()
    if style not in FORECAST_STYLES:
        style = DEFAULT_FORECAST_STYLE
    return style, FORECAST_STYLES[style]


def resolve_colors(config):
    """Return the series colour map, user overrides merged over the defaults."""
    colors = dict(DEFAULT_COLORS)
    override = config.get("colors")
    if isinstance(override, dict):
        for key, value in override.items():
            if key in colors and isinstance(value, str) and value.strip():
                colors[key] = value.strip()
    return colors


def style_chart_series(card, colors, forecast_style):
    """Apply the configured colours and forecast look to a plotly card."""
    for series in card.get("entities", []) or []:
        if not isinstance(series, dict):
            continue
        name = series.get("name")
        color = colors.get(SERIES_COLOR_ROLES.get(name, ""))
        if name in FORECAST_SERIES_NAMES:
            # The wave forecast keeps its own colour (it is the only value
            # series of that chart); only its shape follows `forecast_style`.
            previous = series.get("marker", {}).get("color") or series.get("line", {}).get("color")
            color = color or previous
            if forecast_style["mode"] == "markers":
                series.pop("line", None)
                series["mode"] = "markers"
                marker = series.setdefault("marker", {})
                marker["size"] = marker.get("size", 6)
                marker["symbol"] = forecast_style["symbol"]
                marker["color"] = color
            else:
                series.pop("marker", None)
                series["mode"] = "lines"
                line = series.setdefault("line", {})
                line["width"] = line.get("width", 2)
                line["dash"] = forecast_style["dash"]
                line["color"] = color
            continue
        if not color:
            continue
        if isinstance(series.get("line"), dict):
            series["line"]["color"] = color
        if isinstance(series.get("marker"), dict) and "color" in series["marker"]:
            series["marker"]["color"] = color


def style_glance_tiles(card, colors):
    """Recolour the value tiles so they match their chart series."""
    for tile in card.get("entities", []) or []:
        if not isinstance(tile, dict):
            continue
        role = TILE_COLOR_ROLES.get(tile.get("name"))
        mod = tile.get("card_mod")
        if not role or not isinstance(mod, dict) or not isinstance(mod.get("style"), str):
            continue
        mod["style"] = re.sub(
            r"color: #[0-9a-fA-F]{6} !important",
            f"color: {colors[role]} !important",
            mod["style"],
        )


def strip_leading_line_comments(js_code):
    """Strip a leading block of `//` documentation comment lines.

    src/js/common/*.js files start with a `//` doc-comment block explaining
    what the snippet does (English docs, per project conventions). That
    block is only meant for humans reading the source file — inlining it
    into the `$fn ...` scalar would make the generated dashboard YAML diverge
    from what is actually deployed/live on Home Assistant (which only ever
    saw the bare expression), so it's dropped before wrapping the snippet
    back into a `$fn` value.
    """
    lines = js_code.splitlines()
    i = 0
    while i < len(lines) and lines[i].strip().startswith("//"):
        i += 1
    return "\n".join(lines[i:]).strip()


def load_common_js_snippets():
    """Load all shared JS snippets from src/js/common/ keyed by file stem.

    Each snippet is expected to contain a single JS expression body (e.g. an
    arrow function) that gets wrapped back into a `$fn ...` scalar when
    injected into the dashboard YAML, so the resulting YAML stays equivalent
    to what used to be copy-pasted inline in each section. Leading `//` doc
    comments are stripped so the injected code matches byte-for-byte what
    was previously duplicated inline (and what is live on Home Assistant).
    """
    snippets = {}
    if not os.path.isdir(COMMON_JS_DIR):
        return snippets
    for fname in sorted(os.listdir(COMMON_JS_DIR)):
        if not fname.endswith(".js"):
            continue
        fpath = os.path.join(COMMON_JS_DIR, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            snippets[fname[:-3]] = strip_leading_line_comments(f.read())
    return snippets


def resolve_includes(node, snippets, key_name=None):
    """Recursively replace `$include:<name>` scalar placeholders in-place.

    Walks dicts/lists produced by yaml.safe_load and substitutes any string
    value that equals `$include:<name>` with the shared JS snippet wrapped
    back into a `$fn ...` expression, so multiple sections can reuse the same
    JS code without duplicating it in every YAML file.

    Exception: a plotly-graph ``filters: - fn:`` step already *is* a JS
    function body — the card evaluates it directly and does NOT accept the
    `$fn ` marker (which only tags *config values* that must be evaluated).
    Snippets included under an ``fn`` key are therefore inlined verbatim.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and value.startswith(INCLUDE_PREFIX):
                name = value[len(INCLUDE_PREFIX):]
                if name in snippets:
                    prefix = "" if key in RAW_INCLUDE_KEYS else FN_PREFIX
                    node[key] = prefix + snippets[name]
            else:
                resolve_includes(value, snippets, key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if isinstance(value, str) and value.startswith(INCLUDE_PREFIX):
                name = value[len(INCLUDE_PREFIX):]
                if name in snippets:
                    prefix = "" if key_name in RAW_INCLUDE_KEYS else FN_PREFIX
                    node[i] = prefix + snippets[name]
            else:
                resolve_includes(value, snippets, key_name)


def ensure_dirs():
    """Ensure destination directories exist."""
    os.makedirs(os.path.join(BUILD_DIR, "cards"), exist_ok=True)
    os.makedirs(os.path.join(BUILD_DIR, "local-preview"), exist_ok=True)


def build_cards():
    """Copy JS custom cards to build/cards/."""
    cards_dir = os.path.join(SRC_DIR, "js", "cards")
    dst_dir = os.path.join(BUILD_DIR, "cards")
    if os.path.exists(cards_dir):
        for fname in sorted(os.listdir(cards_dir)):
            if fname.endswith(".js"):
                src_card = os.path.join(cards_dir, fname)
                dst_card = os.path.join(dst_dir, fname)
                shutil.copyfile(src_card, dst_card)
                print(f"Built {dst_card}")


def build_sensors(config=None):
    """Build build/sensors-sailing.yaml from every src/yaml/sensors/*.yaml.

    The modules are merged per top-level key (``rest``, ``template``, ...):
    plain text concatenation would emit the same key twice, and both HA and
    deploy_sensors.sh load the artifact with yaml.safe_load, which silently
    keeps only the last occurrence — that is how the forecast template
    sensors used to vanish from the deployed configuration.
    """
    if config is None:
        config = load_config()

    sensors_dir = os.path.join(SRC_DIR, "yaml", "sensors")

    merged = {}
    for fname in sorted(os.listdir(sensors_dir)):
        if not fname.endswith(".yaml"):
            continue
        fpath = os.path.join(sensors_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise SystemExit(f"{fpath}: expected a mapping at the top level")
        for key, value in data.items():
            section = value if isinstance(value, list) else [value]
            merged.setdefault(key, []).extend(section)

    tw = config.get("time_window", {})
    history_hours = int(tw.get("history_hours", 4))
    forecast_days = int(tw.get("forecast_days", 3))
    forecast_hours = forecast_days * 24

    if "template" in merged and isinstance(merged["template"], list):
        for t_item in merged["template"]:
            if isinstance(t_item, dict) and "sensor" in t_item and isinstance(t_item["sensor"], list):
                for s_entry in t_item["sensor"]:
                    if isinstance(s_entry, dict) and s_entry.get("unique_id") == "chart_time_window":
                        s_entry["attributes"] = {
                            "history_hours": f"{{{{ {history_hours} }}}}",
                            "forecast_hours": f"{{{{ {forecast_hours} }}}}",
                        }

    if "rest" in merged and isinstance(merged["rest"], list):
        for r_item in merged["rest"]:
            if isinstance(r_item, dict) and "resource_template" in r_item:
                r_item["resource_template"] = re.sub(
                    r"int\(\d+\)", f"int({forecast_hours})", r_item["resource_template"]
                )

    dst_path = os.path.join(BUILD_DIR, "sensors-sailing.yaml")
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write("# Generated by build.py — DO NOT EDIT MANUALLY\n")
        yaml.dump(merged, f, sort_keys=False, allow_unicode=True, width=1000)
    print(f"Built {dst_path}")


def build_automations():
    """Build build/automations-sailing.yaml from src/yaml/automations/*.yaml."""
    auto_dir = os.path.join(SRC_DIR, "yaml", "automations")
    dst_path = os.path.join(BUILD_DIR, "automations-sailing.yaml")

    lines = ["# Generated by build.py — DO NOT EDIT MANUALLY\n"]
    for fname in sorted(os.listdir(auto_dir)):
        if fname.endswith(".yaml"):
            fpath = os.path.join(auto_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                lines.append(f.read().strip())
            lines.append("\n\n")

    with open(dst_path, "w", encoding="utf-8") as f:
        f.write("".join(lines).strip() + "\n")
    print(f"Built {dst_path}")


def build_resources():
    """Build build/lovelace-resources.yaml from src/yaml/resources/lovelace-resources.yaml.

    Every `/local/<name>.js?v=...` entry that corresponds to one of our own
    custom cards (src/js/cards/<name>.js) gets its `?v=` cache-buster
    replaced with a short sha256 hash of that file's *current* content.
    This makes the browser's ES-module cache bust automatically whenever a
    card's source changes, instead of relying on a developer remembering to
    bump a hand-written version string (which is easy to forget and was the
    root cause of a stale-card-in-browser bug: the file on disk/deployed
    was already up to date, but the URL never changed so the browser kept
    serving the old cached module bytes).
    """
    src_res = os.path.join(SRC_DIR, "yaml", "resources", "lovelace-resources.yaml")
    dst_res = os.path.join(BUILD_DIR, "lovelace-resources.yaml")
    if not os.path.exists(src_res):
        return

    cards_dir = os.path.join(SRC_DIR, "js", "cards")
    with open(src_res, "r", encoding="utf-8") as f:
        resources = yaml.safe_load(f) or {}

    items = resources.get("resources") if isinstance(resources, dict) else None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith("/local/"):
                continue
            base = url.split("?", 1)[0]
            card_name = base[len("/local/"):]
            card_path = os.path.join(cards_dir, card_name)
            if not os.path.isfile(card_path):
                continue
            with open(card_path, "rb") as cf:
                digest = hashlib.sha256(cf.read()).hexdigest()[:8]
            item["url"] = f"{base}?v={digest}"

    with open(dst_res, "w", encoding="utf-8") as f:
        yaml.safe_dump(resources, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    print(f"Built {dst_res}")


def build_dashboard(config=None):
    """Build build/dashboard-sailing.yaml from header and sections."""
    if config is None:
        config = load_config()

    header_path = os.path.join(SRC_DIR, "yaml", "dashboard", "header.yaml")
    sections_dir = os.path.join(SRC_DIR, "yaml", "dashboard", "sections")

    with open(header_path, "r", encoding="utf-8") as f:
        header_data = yaml.safe_load(f)

    sec_configs = config.get("sections", {})
    tw = config.get("time_window", {})
    history_hours = int(tw.get("history_hours", 4))
    forecast_days = int(tw.get("forecast_days", 3))
    forecast_hours = forecast_days * 24
    total_hours = history_hours + forecast_hours

    _chart_style, arrow_layout = resolve_chart_style(config)
    arrow_spacing = int(config.get("arrow_spacing_hours", 3))
    arrow_length_scale = float(config.get("arrow_length_scale", DEFAULT_ARROW_LENGTH_SCALE))
    if arrow_length_scale.is_integer():
        # Keep the emitted YAML clean: `3` instead of `3.0`.
        arrow_length_scale = int(arrow_length_scale)
    measured_on_line = bool(
        config.get("measured_arrows_on_line", DEFAULT_MEASURED_ARROWS_ON_LINE)
    )
    now_label_opacity = float(config.get("now_label_opacity", DEFAULT_NOW_LABEL_OPACITY))
    forecast_history_arrow_opacity = float(
        config.get(
            "forecast_history_arrow_opacity", DEFAULT_FORECAST_HISTORY_ARROW_OPACITY
        )
    )
    _forecast_style_name, forecast_style = resolve_forecast_style(config)
    colors = resolve_colors(config)

    sections = []
    for fname in sorted(os.listdir(sections_dir)):
        if not fname.endswith(".yaml"):
            continue

        sec_key = re.sub(r"^\d+_", "", fname[:-5])
        sec_cfg = sec_configs.get(sec_key, {})

        if sec_cfg.get("enabled", True) is False:
            continue

        fpath = os.path.join(sections_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            sec_data = yaml.safe_load(f)

        card_toggles = sec_cfg.get("cards", {})

        if isinstance(sec_data, list):
            sec_items = sec_data
        elif isinstance(sec_data, dict):
            sec_items = [sec_data]
        else:
            sec_items = []

        for item in sec_items:
            if isinstance(item, dict) and "cards" in item and isinstance(item["cards"], list):
                filtered_cards = []
                for card in item["cards"]:
                    if isinstance(card, dict):
                        if "id" in card:
                            card_id = card.pop("id")
                            if card_toggles.get(card_id, True) is False:
                                continue
                        if card.get("type") == "custom:plotly-graph":
                            card["hours_to_show"] = total_hours
                            card["time_offset"] = f"{forecast_hours}h"
                            arrow_kind = SECTION_ARROW_KINDS.get(sec_key)
                            if arrow_kind:
                                # Both chart styles are the same Plotly card —
                                # only the shared arrow annotation layer
                                # differs, and it reads these options at
                                # runtime via plotly-graph's getFromConfig().
                                card["arrow_layout"] = arrow_layout
                                card["arrow_spacing_hours"] = arrow_spacing
                                card["arrow_length_scale"] = arrow_length_scale
                                card["measured_arrows_on_line"] = measured_on_line
                                card["now_label_opacity"] = now_label_opacity
                                card["forecast_history_arrow_opacity"] = (
                                    forecast_history_arrow_opacity
                                )
                                card["arrow_kind"] = arrow_kind
                            style_chart_series(card, colors, forecast_style)
                        elif card.get("type") == "glance":
                            style_glance_tiles(card, colors)
                    filtered_cards.append(card)
                item["cards"] = filtered_cards

        sections.extend(sec_items)

    snippets = load_common_js_snippets()
    resolve_includes(sections, snippets)

    if header_data and "views" in header_data and len(header_data["views"]) > 0:
        header_data["views"][0]["sections"] = sections

    dst_path = os.path.join(BUILD_DIR, "dashboard-sailing.yaml")
    with open(dst_path, "w", encoding="utf-8") as f:
        yaml.dump(header_data, f, sort_keys=False, allow_unicode=True)
    print(f"Built {dst_path}")


def main():
    """Main build entry point."""
    print("Starting sailing-dash build...")
    config = load_config()
    ensure_dirs()
    build_cards()
    build_sensors(config)
    build_automations()
    build_resources()
    build_dashboard(config)
    print("Build completed successfully!")


if __name__ == "__main__":
    main()
