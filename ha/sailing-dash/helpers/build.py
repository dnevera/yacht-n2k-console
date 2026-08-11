#!/usr/bin/env python3
"""
Sailing Dashboard Build Script.

Compiles modular YAML files and JS components from src/ into build/ artifacts,
and generates build/local-preview/card-configs.js for offline testing.
"""

import json
import os
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

# Prefix used by `$fn` scalars in dashboard YAML (evaluated by plotly-graph /
# ha-yaml-templating at runtime).
FN_PREFIX = "$fn "
# Prefix used by scalar placeholders in dashboard YAML to reference a shared
# JS snippet from src/js/common/ instead of duplicating it inline.
INCLUDE_PREFIX = "$include:"
# YAML keys whose value is raw JS evaluated by the card itself — an included
# snippet must NOT be wrapped into `$fn ...` there (plotly-graph's `filters`
# steps are the only such place today).
RAW_INCLUDE_KEYS = {"fn"}


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
    src_card = os.path.join(SRC_DIR, "js", "cards", "windy-boat-card.js")
    dst_card = os.path.join(BUILD_DIR, "cards", "windy-boat-card.js")
    if os.path.exists(src_card):
        shutil.copyfile(src_card, dst_card)
        print(f"Built {dst_card}")


def build_sensors():
    """Build build/sensors-sailing.yaml from every src/yaml/sensors/*.yaml.

    The modules are merged per top-level key (``rest``, ``template``, ...):
    plain text concatenation would emit the same key twice, and both HA and
    deploy_sensors.sh load the artifact with yaml.safe_load, which silently
    keeps only the last occurrence — that is how the forecast template
    sensors used to vanish from the deployed configuration.
    """
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
    """Build build/lovelace-resources.yaml from src/yaml/resources/lovelace-resources.yaml."""
    src_res = os.path.join(SRC_DIR, "yaml", "resources", "lovelace-resources.yaml")
    dst_res = os.path.join(BUILD_DIR, "lovelace-resources.yaml")
    if os.path.exists(src_res):
        shutil.copyfile(src_res, dst_res)
        print(f"Built {dst_res}")


def build_dashboard():
    """Build build/dashboard-sailing.yaml from header and sections."""
    header_path = os.path.join(SRC_DIR, "yaml", "dashboard", "header.yaml")
    sections_dir = os.path.join(SRC_DIR, "yaml", "dashboard", "sections")

    with open(header_path, "r", encoding="utf-8") as f:
        header_data = yaml.safe_load(f)

    sections = []
    for fname in sorted(os.listdir(sections_dir)):
        if fname.endswith(".yaml"):
            fpath = os.path.join(sections_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                sec_data = yaml.safe_load(f)
                if isinstance(sec_data, list):
                    sections.extend(sec_data)
                elif isinstance(sec_data, dict):
                    sections.append(sec_data)

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
    ensure_dirs()
    build_cards()
    build_sensors()
    build_automations()
    build_resources()
    build_dashboard()
    print("Build completed successfully!")


if __name__ == "__main__":
    main()
