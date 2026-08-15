#!/usr/bin/env python3
"""
provision_helpers.py — idempotently ensure the Lovelace helpers the AIS
dashboard needs exist in a live Home Assistant instance.

Two helpers are provisioned:

  * `input_boolean.ais_table_expanded` — drives the target-list overlay on the
    AIS map: OFF renders the compact side-bar (vessel name + distance), ON
    expands it into the full table.
  * `input_text.ais_selected_mmsi` — MMSI of the target whose detail card is
    shown. Home Assistant renders NOTHING inside the more-info dialog of a
    `geo_location` entity (the domain has no more-info control and
    `more-info-default` renders `nothing`), so clicking a target used to show
    its bare state only. The table therefore SELECTS the target into this
    helper and the dashboard renders a full detail card from it.

UI-created helpers live in `.storage/<domain>`, so this script merges the
entries into those documents — same read / compute / write-only-if-changed
convention as patch_pgn_include.py and
ha/sailing-dash/helpers/merge_lovelace_resources.py.

USAGE
    python3 provision_helpers.py <.storage/input_boolean path> [--write] \
        [--input-text <.storage/input_text path>] \
        [--entity-registry <.storage/core.entity_registry path>]

    Without --write: report only; exit 0 if nothing needs to change, 2 if it
    does (so deploy.sh can branch without parsing stdout).
    With --write: patches (or creates) the file(s) in place, idempotently.

A missing file is fine: HA creates `.storage/input_boolean` only once a helper
exists, so this script writes a fresh, minimally-valid document in that case.

PER-USER HELPERS (`--auth`)
Home Assistant has NO per-user entity state: every dashboard helper is global,
so one user's selection was visible to everybody. The dashboard therefore uses
ONE HELPER PER USER — `input_text.ais_selected_mmsi_<user_slug>` and
`input_boolean.ais_table_expanded_<user_slug>` — and the Lovelace side picks the
current user's helper at render time (see src/js/ais-select-bridge.js, card
`custom:ais-user-scope`). Pass `--auth <.storage/auth>` to provision one pair per
active, non-system user. The un-suffixed helpers are still provisioned as the
fallback used when the user cannot be resolved.

IMPORTANT — the entity_id comes from `name`, not from `id`.
Home Assistant slugifies the helper's DISPLAY NAME to build its entity_id, so
the name MUST be "AIS table expanded" to yield
`input_boolean.ais_table_expanded`. An earlier revision shipped the name "AIS
Targets", which produced `input_boolean.ais_targets` instead — the dashboard's
two `conditional` cards then never matched and the whole target-list overlay
stayed invisible. Renaming alone cannot fix an already-registered helper (the
entity_id is pinned in `core.entity_registry`), hence `--entity-registry`: it
drops the mis-slugged registry row so HA re-registers it correctly on restart.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HELPER_ID = "ais_table_expanded"
HELPER_ENTITY_ID = f"input_boolean.{HELPER_ID}"
HELPER_CONFIG = {
    "id": HELPER_ID,
    # Must slugify to HELPER_ID — see the module docstring.
    "name": "AIS table expanded",
    "icon": "mdi:table-eye",
}
# Selected-target helper: holds the MMSI whose detail card is rendered.
# `max` is generous on purpose — an MMSI is 9 digits, but the helper is also
# cleared to an empty string by the detail card's close button.
SELECTED_ID = "ais_selected_mmsi"
SELECTED_ENTITY_ID = f"input_text.{SELECTED_ID}"
SELECTED_CONFIG = {
    "id": SELECTED_ID,
    # Must slugify to SELECTED_ID — see the module docstring.
    "name": "AIS selected mmsi",
    "icon": "mdi:ferry",
    "min": 0,
    "max": 16,
    # `mode` is MANDATORY in the storage schema: without it HA aborts the whole
    # input_text component with "Error during setup of component input_text:
    # 'mode'", so the helper (and the detail card that depends on it) never
    # appears.
    "mode": "text",
}


def slugify(value: str) -> str:
    """Same shape as Home Assistant's slugify for our purposes: lowercase,
    every run of non-alphanumerics collapsed into a single underscore."""
    out = []
    prev_us = True
    for ch in value.lower():
        if ch.isalnum() and ch.isascii():
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_")


def user_slug(user: dict) -> str:
    """Slug identifying a user's helpers. Mirrors the JS side exactly: the
    slugified name, or `u_<first 8 chars of the user id>` when the name has no
    ASCII alphanumerics (e.g. a fully cyrillic name)."""
    slug = slugify(str(user.get("name") or ""))
    if slug:
        return slug
    return "u_" + str(user.get("id") or "")[:8]


def user_helper_configs(slug: str) -> list[tuple[dict, str, str]]:
    """`[(config, entity_id, store_key), ...]` for one user's helper pair."""
    toggle_id = f"{HELPER_ID}_{slug}"
    selected_id = f"{SELECTED_ID}_{slug}"
    return [
        (
            {
                "id": toggle_id,
                # Must slugify to toggle_id — see the module docstring.
                "name": f"AIS table expanded {slug}",
                "icon": HELPER_CONFIG["icon"],
            },
            f"input_boolean.{toggle_id}",
            "input_boolean",
        ),
        (
            {
                "id": selected_id,
                "name": f"AIS selected mmsi {slug}",
                "icon": SELECTED_CONFIG["icon"],
                "min": SELECTED_CONFIG["min"],
                "max": SELECTED_CONFIG["max"],
                "mode": SELECTED_CONFIG["mode"],
            },
            f"input_text.{selected_id}",
            "input_text",
        ),
    ]


def load_users(auth_path: str) -> list[dict]:
    """Active, non-system users from `.storage/auth`."""
    with open(auth_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    users = doc.get("data", {}).get("users") or []
    return [
        u
        for u in users
        if isinstance(u, dict)
        and u.get("is_active", True)
        and not u.get("system_generated")
    ]


def _empty_doc(key: str) -> dict:
    return {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"items": []},
    }


EMPTY_DOC = _empty_doc("input_boolean")


def compute_patch(
    doc: dict, config: dict | None = None, entity_id: str | None = None
) -> tuple[bool, list[str]]:
    """Return `(changed, messages)`, mutating `doc` in place."""
    config = config or HELPER_CONFIG
    entity_id = entity_id or HELPER_ENTITY_ID
    helper_id = config["id"]
    messages: list[str] = []
    data = doc.setdefault("data", {})
    items = data.setdefault("items", [])

    for item in items:
        if isinstance(item, dict) and item.get("id") == helper_id:
            # Every key is compared, not just `name`: an entry written by an
            # older revision can be missing a MANDATORY key (input_text without
            # `mode` aborts the whole component at startup), and that must be
            # repaired in place rather than reported as "already exists".
            missing = {k: v for k, v in config.items() if item.get(k) != v}
            if not missing:
                messages.append(f"{entity_id} already exists — no change.")
                return False, messages
            item.update(missing)
            messages.append(
                f"Updated {entity_id} ({', '.join(sorted(missing))}) — the name "
                "must slugify to the entity_id the dashboard references."
            )
            return True, messages

    items.append(dict(config))
    messages.append(f"Added {entity_id}.")
    return True, messages


def compute_registry_patch(
    doc: dict,
    platform: str = "input_boolean",
    helper_id: str = HELPER_ID,
    entity_id: str = HELPER_ENTITY_ID,
) -> tuple[bool, list[str]]:
    """Drop a mis-slugged registry row for our helper, so HA re-registers it
    under HELPER_ENTITY_ID on the next start. Mutates `doc` in place."""
    messages: list[str] = []
    entities = doc.get("data", {}).get("entities")
    if not isinstance(entities, list):
        return False, ["entity registry has no 'entities' list — skipped."]

    stale = [
        e
        for e in entities
        if isinstance(e, dict)
        and e.get("platform") == platform
        and e.get("unique_id") == helper_id
        and e.get("entity_id") != entity_id
    ]
    if not stale:
        messages.append(f"entity registry: no stale row for {helper_id}.")
        return False, messages

    for entry in stale:
        entities.remove(entry)
        messages.append(
            f"entity registry: removed mis-slugged row {entry.get('entity_id')} "
            f"— HA will re-register it as {entity_id}."
        )
    return True, messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_boolean_path", help="path to .storage/input_boolean")
    parser.add_argument(
        "--write",
        action="store_true",
        help="patch the file in place instead of only reporting",
    )
    parser.add_argument(
        "--input-text",
        help="path to .storage/input_text (selected-target helper)",
    )
    parser.add_argument(
        "--entity-registry",
        help="path to .storage/core.entity_registry, to drop a mis-slugged row",
    )
    parser.add_argument(
        "--auth",
        help="path to .storage/auth — provisions one helper pair per user",
    )
    args = parser.parse_args()

    users = []
    if args.auth and os.path.exists(args.auth):
        users = load_users(args.auth)
        print(
            "per-user helpers for: "
            + ", ".join(sorted(user_slug(u) for u in users))
        )
    elif args.auth:
        print(f"{args.auth} does not exist — per-user helpers skipped.")

    if os.path.exists(args.input_boolean_path):
        with open(args.input_boolean_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = json.loads(json.dumps(EMPTY_DOC))
        print(
            f"{args.input_boolean_path} does not exist yet — a fresh "
            "input_boolean store will be created."
        )

    changed, messages = compute_patch(doc)
    for message in messages:
        print(message)
    if not os.path.exists(args.input_boolean_path):
        changed = True

    # Per-user toggles live in the same input_boolean store as the fallback.
    for user in users:
        for config, entity_id, store in user_helper_configs(user_slug(user)):
            if store != "input_boolean":
                continue
            patched, user_messages = compute_patch(doc, config, entity_id)
            changed = changed or patched
            for message in user_messages:
                print(message)

    text_doc = None
    text_changed = False
    if args.input_text:
        if os.path.exists(args.input_text):
            with open(args.input_text, "r", encoding="utf-8") as f:
                text_doc = json.load(f)
        else:
            text_doc = _empty_doc("input_text")
            text_changed = True
            print(
                f"{args.input_text} does not exist yet — a fresh input_text "
                "store will be created."
            )
        patched, text_messages = compute_patch(
            text_doc, SELECTED_CONFIG, SELECTED_ENTITY_ID
        )
        text_changed = text_changed or patched
        for message in text_messages:
            print(message)
        for user in users:
            for config, entity_id, store in user_helper_configs(user_slug(user)):
                if store != "input_text":
                    continue
                patched, user_messages = compute_patch(text_doc, config, entity_id)
                text_changed = text_changed or patched
                for message in user_messages:
                    print(message)

    reg_doc = None
    reg_changed = False
    if args.entity_registry and os.path.exists(args.entity_registry):
        with open(args.entity_registry, "r", encoding="utf-8") as f:
            reg_doc = json.load(f)
        reg_changed, reg_messages = compute_registry_patch(reg_doc)
        for message in reg_messages:
            print(message)
        sel_changed, sel_messages = compute_registry_patch(
            reg_doc, "input_text", SELECTED_ID, SELECTED_ENTITY_ID
        )
        reg_changed = reg_changed or sel_changed
        for message in sel_messages:
            print(message)

    if not changed and not text_changed and not reg_changed:
        return 0

    if args.write:
        if changed:
            with open(args.input_boolean_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"Wrote {args.input_boolean_path}")
        if text_changed and text_doc is not None:
            with open(args.input_text, "w", encoding="utf-8") as f:
                json.dump(text_doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"Wrote {args.input_text}")
        if reg_changed and reg_doc is not None:
            with open(args.entity_registry, "w", encoding="utf-8") as f:
                json.dump(reg_doc, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"Wrote {args.entity_registry}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
