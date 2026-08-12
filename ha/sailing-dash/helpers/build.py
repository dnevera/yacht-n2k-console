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
                if "time_window" in override and isinstance(override["time_window"], dict):
                    config.setdefault("time_window", {}).update(override["time_window"])
                if "sections" in override and isinstance(override["sections"], dict):
                    config.setdefault("sections", {})
                    for sec_key, sec_val in override["sections"].items():
                        if isinstance(sec_val, dict):
                            target_sec = config["sections"].setdefault(sec_key, {})
                            # Any scalar section option (enabled, chart_style,
                            # arrow_spacing_hours, ...) overrides the template
                            # as-is; `cards` is merged key-by-key so a partial
                            # config.yaml keeps the template's other toggles.
                            for opt_key, opt_val in sec_val.items():
                                if opt_key == "cards":
                                    continue
                                target_sec[opt_key] = opt_val
                            if "cards" in sec_val and isinstance(sec_val["cards"], dict):
                                target_sec.setdefault("cards", {}).update(sec_val["cards"])

    return config


# Wind chart styles. Both are rendered by the same `custom:plotly-graph` card —
# the only difference is where the wind-direction arrow annotations are drawn,
# which `src/js/common/plotly_wind_annotations.js` decides from the injected
# `arrow_layout` option:
#   plotly     -> on_point: an arrow sits on each data point of the speed line.
#   open_meteo -> top_row : arrows line up in one row under the chart's top
#                 edge (open-meteo.com preview style), values stay as lines.
WIND_CHART_STYLES = {
    "plotly": "on_point",
    "open_meteo": "top_row",
}
DEFAULT_WIND_CHART_STYLE = "open_meteo"


def resolve_wind_chart_style(wind_cfg):
    """Return `(style, arrow_layout)` for the wind section config.

    Accepts the legacy `chart_engine` key as an alias of `chart_style` so an
    existing config.yaml keeps working; the retired `apexcharts` /
    `open_meteo_sdk` engines map onto the closest surviving Plotly style
    instead of failing the build.
    """
    raw = wind_cfg.get("chart_style", wind_cfg.get("chart_engine"))
    style = str(raw if raw is not None else DEFAULT_WIND_CHART_STYLE).strip().lower()
    legacy = {
        "apexcharts": "open_meteo",
        "open_meteo_sdk": "open_meteo",
        "openmeteo": "open_meteo",
    }
    style = legacy.get(style, style)
    if style not in WIND_CHART_STYLES:
        style = DEFAULT_WIND_CHART_STYLE
    return style, WIND_CHART_STYLES[style]


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

    wind_cfg = sec_configs.get("wind", {})
    wind_style, wind_arrow_layout = resolve_wind_chart_style(wind_cfg)
    wind_arrow_spacing = int(wind_cfg.get("arrow_spacing_hours", 3))

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
                            if sec_key == "wind":
                                # Both wind chart styles are the same Plotly
                                # card — only the wind-arrow annotation layer
                                # differs, and it reads these two options at
                                # runtime via plotly-graph's getFromConfig().
                                card["arrow_layout"] = wind_arrow_layout
                                card["arrow_spacing_hours"] = wind_arrow_spacing
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
