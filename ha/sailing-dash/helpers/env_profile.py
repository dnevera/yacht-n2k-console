#!/usr/bin/env python3
"""env_profile.py — Python counterpart of lib/env_profile.sh.

Resolves a NAMED TARGET PROFILE from ha/sailing-dash/.env (see .env.template).
This subproject is self-contained: the repo root .env / deploy.conf belong to the
ydnu-02 manager and are never read from here.

    from env_profile import load_profile
    prof = load_profile("stage")     # prof.container, prof.gw_host, prof.ha_url, …

CLI:
    python3 env_profile.py stage             # print the resolved profile as JSON
    python3 env_profile.py --list            # print known profile names
"""

import argparse
import json
import os
import re
import sys
from typing import Dict, Optional

# This module lives in ha/sailing-dash/helpers/; .env belongs to the subproject
# root one level up.
HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(HELPERS_DIR)
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
ENV_TEMPLATE = os.path.join(SCRIPT_DIR, ".env.template")

_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')
_DEFAULT_RE = re.compile(r'^\$\{([A-Za-z_][A-Za-z0-9_]*):-(.*)\}$')

DEFAULTS = {
    "stage": {"transport": "local-docker", "container": "local-ha"},
    "prod": {"transport": "ssh-docker", "container": "homeassistant"},
}


class Profile:
    """A resolved target profile: where HA lives and which tcp-gw it talks to."""

    def __init__(self, name: str, values: Dict[str, str]):
        self.name = name
        default = DEFAULTS.get(name, {})
        self.transport = values.get("TRANSPORT") or default.get("transport", "ssh-docker")
        self.ssh_host = values.get("SSH_HOST", "")
        self.container = values.get("CONTAINER") or default.get("container", "homeassistant")
        self.config_dir = values.get("CONFIG_DIR") or "/config"
        self.ha_url = values.get("HA_URL") or ""
        self.ha_token = values.get("HA_TOKEN") or ""
        self.gw_host = values.get("GW_HOST") or "127.0.0.1"
        try:
            self.gw_data_port = int(values.get("GW_DATA_PORT") or 4001)
        except ValueError:
            self.gw_data_port = 4001

    def as_dict(self) -> Dict[str, object]:
        return {
            "profile": self.name,
            "transport": self.transport,
            "ssh_host": self.ssh_host,
            "container": self.container,
            "config_dir": self.config_dir,
            "ha_url": self.ha_url,
            "ha_token": "***" if self.ha_token else "",
            "gw_host": self.gw_host,
            "gw_data_port": self.gw_data_port,
        }


def _parse_env_file(path: str) -> Dict[str, str]:
    """Reads the .env shell fragment.

    Values are written as VAR="${VAR:-default}" so that a real environment
    variable still wins — exactly as when bash sources the same file.
    """
    values: Dict[str, str] = {}
    if not os.path.isfile(path):
        return values

    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = _ASSIGN_RE.match(line)
            if not match:
                continue
            key, raw = match.group(1), match.group(2)
            if raw[:1] in ('"', "'") and raw[-1:] == raw[:1] and len(raw) >= 2:
                raw = raw[1:-1]
            fallback = _DEFAULT_RE.match(raw)
            if fallback:
                values[key] = os.environ.get(fallback.group(1), fallback.group(2))
            else:
                values[key] = os.environ.get(key, raw)
    return values


def profile_prefix(name: str) -> str:
    return name.upper().replace("-", "_")


def known_profiles() -> list:
    env = _parse_env_file(ENV_FILE)
    declared = os.environ.get("HA_PROFILES") or env.get("HA_PROFILES") or "stage prod"
    return declared.split()


def load_profile(name: Optional[str] = None) -> Profile:
    name = name or os.environ.get("HA_PROFILE") or "stage"
    env = _parse_env_file(ENV_FILE)

    if name not in known_profiles():
        raise SystemExit(
            "ERROR: unknown target profile '%s'. Known profiles: %s\n"
            "       Declare it in HA_PROFILES inside %s (cp %s %s)."
            % (name, " ".join(known_profiles()), ENV_FILE, ENV_TEMPLATE, ENV_FILE)
        )

    prefix = profile_prefix(name) + "_"
    values: Dict[str, str] = {}
    for key, value in env.items():
        if key.startswith(prefix):
            values[key[len(prefix):]] = value
    # A plain environment variable overrides .env even when .env is absent.
    for suffix in ("TRANSPORT", "SSH_HOST", "CONTAINER", "CONFIG_DIR",
                   "HA_URL", "HA_TOKEN", "GW_HOST", "GW_DATA_PORT"):
        override = os.environ.get(prefix + suffix)
        if override:
            values[suffix] = override

    return Profile(name, values)


def main():
    parser = argparse.ArgumentParser(description="Resolve a sailing-dash target profile")
    parser.add_argument("profile", nargs="?", default=None, help="Profile name (default: stage)")
    parser.add_argument("--list", action="store_true", help="List known profile names")
    args = parser.parse_args()

    if args.list:
        print(" ".join(known_profiles()))
        return 0

    print(json.dumps(load_profile(args.profile).as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
