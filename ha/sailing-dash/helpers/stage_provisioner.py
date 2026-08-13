#!/usr/bin/env python3
"""
Home Assistant Stage Provisioner Engine (ha/sailing-dash/stage_provisioner.py)

Handles Home Assistant state inspection, onboarding bypass, .storage registry
initialization (dashboards & resources), card bundle deployment, and post-launch
HTTP readiness checks.
"""

import os
import sys
import io
import json
import uuid
import time
import hashlib
import base64
import shutil
import zipfile
import tempfile
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import argparse
from typing import Dict, List, Any, Optional, Tuple

from merge_lovelace_resources import merge_registry
from env_profile import load_profile

# This script lives in ha/sailing-dash/helpers/; build/, src/, local-ha/ and .env
# belong to the subproject root one level up.
HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(HELPERS_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
DEPS_DIR = os.path.join(SCRIPT_DIR, "build", "deps")
DEPS_CARDS_DIR = os.path.join(DEPS_DIR, "cards")
FETCH_DEPS = os.path.join(HELPERS_DIR, "fetch_deps.py")
CARDS_BUILD_DIR = os.path.join(SCRIPT_DIR, "build", "cards")
RESOURCES_FILE = os.path.join(SCRIPT_DIR, "build", "lovelace-resources.yaml")
SRC_RESOURCES_FILE = os.path.join(SCRIPT_DIR, "src", "yaml", "resources", "lovelace-resources.yaml")
LOCAL_CONFIG_DIR = os.path.join(SCRIPT_DIR, "local-ha", "config")

# Every external artifact (the "NMEA 2000" integration — OUR OWN fork
# github.com/dnevera/ha-nmea2000 pinned to a tag — HACS itself and the frontend
# card bundles) is declared in deps.yaml and downloaded by fetch_deps.py into
# build/deps/. Nothing is vendored/committed into the repo any more, and there is
# no hidden .cache/: build/deps/ is an ordinary build artifact directory.
# Copying the integration files directly (HACS itself is just a downloader/
# updater) has the identical runtime effect as installing through HACS on Prod.
NMEA2000_INTEGRATION_DEPS_DIR = os.path.join(DEPS_DIR, "nmea2000", "custom_components", "nmea2000")
HACS_INTEGRATION_DEPS_DIR = os.path.join(DEPS_DIR, "hacs", "custom_components", "hacs")
# The target is a NAMED PROFILE from ha/sailing-dash/.env (see .env.template):
# it carries the container name AND the YDNU-02 tcp-gw this instance must talk to.
# Nothing here is hardcoded to local-ha / 127.0.0.1:4001 any more, and the repo
# root .env / deploy.conf (the ydnu-02 manager's own config) is never read.
DEFAULT_PROFILE = os.environ.get("HA_PROFILE") or "stage"
DEFAULT_CONTAINER = os.environ.get("HA_CONTAINER") or load_profile(DEFAULT_PROFILE).container

ALL_REQUIRED_CARDS = [
    "card-mod.js",
    "compass-card.js",
    "apexcharts-card.js",
    "windrose-card.js",
    "plotly-graph-card.js",
    "config-template-card.js",
    "windy-boat-card.js",
]


def log(level: str, msg: str):
    prefix_map = {
        "INFO": "\033[94m[INFO]\033[0m",
        "PROVISION": "\033[92m[PROVISION]\033[0m",
        "WARN": "\033[93m[WARN]\033[0m",
        "ERROR": "\033[91m[ERROR]\033[0m",
    }
    prefix = prefix_map.get(level, f"[{level}]")
    print(f"{prefix} {msg}")


class HAProvisioner:
    """Provisioner engine for Stage/Prod Home Assistant instances."""

    def __init__(self, config_dir: Optional[str] = None, container_name: str = DEFAULT_CONTAINER,
                 profile: Optional[str] = None):
        self.config_dir = os.path.abspath(config_dir) if config_dir else None
        self.container_name = container_name
        # The tcp-gw address of THIS profile — what the nmea2000 config entry points at.
        self.profile = load_profile(profile or DEFAULT_PROFILE)
        self.gw_host = self.profile.gw_host
        self.gw_port = self.profile.gw_data_port
        # A profile may live on another machine: every docker call then has to be
        # tunnelled through SSH, exactly like lib/ha_target.sh does for the shell side.
        self.transport = self.profile.transport
        self.ssh_host = self.profile.ssh_host

        # Fallback to local-ha/config if it exists and config_dir not explicitly passed.
        # Only valid for a local target — a remote container's /config is not on this disk.
        if (not self.config_dir and self.transport == "local-docker"
                and os.path.isdir(LOCAL_CONFIG_DIR)):
            self.config_dir = LOCAL_CONFIG_DIR

        # Delivery is content-addressed: a card bundle or integration file whose
        # sha256 already matches the target is not copied again (deploy.sh --force
        # / HA_FORCE_DELIVERY=1 overrides). Counters are informational only.
        self.force_delivery = os.environ.get("HA_FORCE_DELIVERY", "0") == "1"
        self.delivered_count = 0
        self.skipped_count = 0
        self.last_copy_skipped = False

    # ── Container / File Operations ──────────────────────────────────────────

    def _exec_docker(self, cmd: List[str]) -> Tuple[int, str]:
        """Runs docker exec in the target container, locally or over SSH depending
        on the profile's transport."""
        if self.transport == "ssh-docker" and self.ssh_host:
            remote = "sudo docker exec {} {}".format(
                self.container_name, " ".join(f"'{part}'" for part in cmd))
            full_cmd = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
                        self.ssh_host, remote]
            timeout = 30
        else:
            full_cmd = ["docker", "exec", self.container_name] + cmd
            timeout = 10
        try:
            res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
            return res.returncode, res.stdout
        except Exception as e:
            return 1, str(e)

    def is_container_running(self) -> bool:
        """Returns True if the target docker container exists and is running."""
        try:
            res = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
                capture_output=True, text=True, timeout=10,
            )
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def stop_container(self) -> bool:
        """Stops the target docker container (no-op if not running)."""
        if not self.is_container_running():
            return True
        log("INFO", f"Stopping container {self.container_name} before editing .storage/ "
                     f"(HA flushes its in-memory state back to disk on shutdown, which would "
                     f"otherwise silently overwrite files we edit while it's still running) ...")
        try:
            res = subprocess.run(["docker", "stop", self.container_name], capture_output=True, text=True, timeout=30)
            return res.returncode == 0
        except Exception as e:
            log("ERROR", f"Failed to stop container {self.container_name}: {e}")
            return False

    def start_container(self) -> bool:
        """Starts the target docker container (no-op if already running)."""
        if self.is_container_running():
            return True
        log("INFO", f"Starting container {self.container_name} ...")
        try:
            res = subprocess.run(["docker", "start", self.container_name], capture_output=True, text=True, timeout=30)
            return res.returncode == 0
        except Exception as e:
            log("ERROR", f"Failed to start container {self.container_name}: {e}")
            return False

    def read_config_file(self, rel_path: str) -> Optional[str]:
        """Reads a file from /config inside container or local config_dir."""
        if self.config_dir:
            full_path = os.path.join(self.config_dir, rel_path.lstrip("/"))
            if os.path.isfile(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        return f.read()
                except OSError:
                    return None
            return None

        # Fallback to docker exec
        container_path = os.path.join("/config", rel_path.lstrip("/"))
        code, out = self._exec_docker(["cat", container_path])
        return out if code == 0 else None

    def write_config_file(self, rel_path: str, content: str) -> bool:
        """Writes content to a file in /config inside container or local config_dir."""
        if self.config_dir:
            full_path = os.path.join(self.config_dir, rel_path.lstrip("/"))
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            try:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return True
            except OSError as e:
                log("ERROR", f"Failed to write file {full_path}: {e}")
                return False

        # Fallback to docker exec / cp
        container_path = os.path.join("/config", rel_path.lstrip("/"))
        dir_path = os.path.dirname(container_path)
        self._exec_docker(["mkdir", "-p", dir_path])
        
        # Write via tee
        try:
            proc = subprocess.run(
                ["docker", "exec", "-i", self.container_name, "sh", "-c", f"cat > {container_path}"],
                input=content,
                text=True,
                capture_output=True,
                timeout=10,
            )
            return proc.returncode == 0
        except Exception as e:
            log("ERROR", f"Failed writing to container file {container_path}: {e}")
            return False

    def _target_file_sha256(self, dest_rel_path: str) -> Optional[str]:
        """sha256 of the file as it currently exists on the target, or None."""
        if self.config_dir:
            full_path = os.path.join(self.config_dir, dest_rel_path.lstrip("/"))
            if not os.path.isfile(full_path):
                return None
            try:
                with open(full_path, "rb") as f:
                    return hashlib.sha256(f.read()).hexdigest()
            except OSError:
                return None

        container_path = os.path.join("/config", dest_rel_path.lstrip("/"))
        if self.transport == "ssh-docker" and self.ssh_host:
            cmd = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", self.ssh_host,
                   f"sudo docker exec {self.container_name} cat {container_path}"]
            timeout = 60
        else:
            cmd = ["docker", "exec", self.container_name, "cat", container_path]
            timeout = 20
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except Exception:
            return None
        if res.returncode != 0 or not res.stdout:
            return None
        return hashlib.sha256(res.stdout).hexdigest()

    def copy_card_to_ha(self, src_file: str, dest_rel_path: str) -> bool:
        """Copies a local JS card file to /config/www/... inside HA.

        Skips the copy when the target already holds byte-identical content: a
        provisioning run used to re-push every card bundle and every file of the
        two custom integrations on every deploy.
        """
        if not os.path.isfile(src_file):
            return False

        self.last_copy_skipped = False
        if not self.force_delivery:
            try:
                with open(src_file, "rb") as f:
                    local_hash = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                local_hash = None
            if local_hash and local_hash == self._target_file_sha256(dest_rel_path):
                self.skipped_count += 1
                self.last_copy_skipped = True
                return True
        self.delivered_count += 1

        if self.config_dir:
            dest_full = os.path.join(self.config_dir, dest_rel_path.lstrip("/"))
            os.makedirs(os.path.dirname(dest_full), exist_ok=True)
            try:
                with open(src_file, "rb") as sf, open(dest_full, "wb") as df:
                    df.write(sf.read())
                return True
            except OSError as e:
                log("ERROR", f"Failed copying card to {dest_full}: {e}")
                return False

        # Docker cp fallback
        container_path = os.path.join("/config", dest_rel_path.lstrip("/"))
        dir_path = os.path.dirname(container_path)
        self._exec_docker(["mkdir", "-p", dir_path])

        # A remote target needs two hops: scp the file to the host, then docker cp it
        # into the container from there (same as lib/ha_target.sh does for the shell).
        if self.transport == "ssh-docker" and self.ssh_host:
            staged = f"/tmp/sailing_{uuid.uuid4().hex}"
            try:
                if subprocess.run(["scp", "-q", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
                                   src_file, f"{self.ssh_host}:{staged}"],
                                  capture_output=True, timeout=120).returncode != 0:
                    return False
                res = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", self.ssh_host,
                     f"sudo docker cp {staged} {self.container_name}:{container_path}; "
                     f"rc=$?; rm -f {staged}; exit $rc"],
                    capture_output=True, timeout=120,
                )
                return res.returncode == 0
            except Exception as e:
                log("ERROR", f"Failed delivering {src_file} to {self.ssh_host}: {e}")
                return False

        try:
            res = subprocess.run(
                ["docker", "cp", src_file, f"{self.container_name}:{container_path}"],
                capture_output=True,
                timeout=10,
            )
            return res.returncode == 0
        except Exception as e:
            log("ERROR", f"Failed docker cp for {src_file}: {e}")
            return False

    def copy_dir_to_ha(self, src_dir: str, dest_rel_dir: str) -> bool:
        """Recursively copies a local directory (e.g. a custom_component) into
        /config/<dest_rel_dir>/ inside HA, preserving the directory tree."""
        if not os.path.isdir(src_dir):
            log("ERROR", f"Source directory not found: {src_dir}")
            return False

        all_ok = True
        copied = skipped = 0
        for root, _dirs, files in os.walk(src_dir):
            rel_root = os.path.relpath(root, src_dir)
            for filename in files:
                if filename.startswith("."):
                    continue  # skip our own metadata markers (e.g. .hacs_source.json)
                src_file = os.path.join(root, filename)
                dest_rel_path = os.path.join(
                    dest_rel_dir, filename if rel_root == "." else os.path.join(rel_root, filename)
                )
                if not self.copy_card_to_ha(src_file, dest_rel_path):
                    all_ok = False
                elif self.last_copy_skipped:
                    skipped += 1
                else:
                    copied += 1
        log("INFO", f"{dest_rel_dir}: {copied} file(s) delivered, {skipped} unchanged")
        return all_ok

    # ── Inspection ──────────────────────────────────────────────────────────

    def inspect_ha_environment(self) -> Dict[str, Any]:
        """Inspects current state of target HA container/config directory."""
        status = {
            "config_dir": self.config_dir,
            "container": self.container_name,
            "onboarding_done": False,
            "dashboard_registered": False,
            "resources_registered": False,
            "missing_cards": [],
            "hacs_installed": False,
            "hacs_activated": False,
            "nmea2000_integration_installed": False,
            "nmea2000_configured": False,
            "is_clean_instance": False,
        }

        # Check onboarding status
        onboarding_raw = self.read_config_file(".storage/onboarding")
        if onboarding_raw:
            try:
                data = json.loads(onboarding_raw)
                done_list = data.get("data", {}).get("done", [])
                if "user" in done_list and "core_config" in done_list:
                    status["onboarding_done"] = True
            except json.JSONDecodeError:
                pass

        # Check lovelace_dashboards status
        dashboards_raw = self.read_config_file(".storage/lovelace_dashboards")
        if dashboards_raw:
            try:
                data = json.loads(dashboards_raw)
                items = data.get("data", {}).get("items", [])
                for item in items:
                    if item.get("url_path") == "dashboard-sailing":
                        status["dashboard_registered"] = True
                        break
            except json.JSONDecodeError:
                pass

        # Check lovelace_resources status
        resources_raw = self.read_config_file(".storage/lovelace_resources")
        if resources_raw:
            try:
                data = json.loads(resources_raw)
                items = data.get("data", {}).get("items", [])
                urls = [it.get("url", "") for it in items]
                # Check if at least some required cards are registered
                if any("dashboard-sailing" in u or "card-mod" in u or "plotly" in u for u in urls):
                    status["resources_registered"] = True
            except json.JSONDecodeError:
                pass

        # Check present card JS files in www/
        for card_filename in ALL_REQUIRED_CARDS:
            card_found = False
            card_content = self.read_config_file(f"www/{card_filename}")
            if card_content is not None:
                card_found = True
            else:
                # Check in community subdir
                card_content_comm = self.read_config_file(f"www/community/{card_filename}")
                if card_content_comm is not None:
                    card_found = True

            if not card_found:
                status["missing_cards"].append(card_filename)

        # Check HACS itself (domain "hacs") — Stage never had it installed at all before.
        hacs_manifest_raw = self.read_config_file("custom_components/hacs/manifest.json")
        if hacs_manifest_raw:
            try:
                hacs_manifest = json.loads(hacs_manifest_raw)
                if hacs_manifest.get("domain") == "hacs":
                    status["hacs_installed"] = True
            except json.JSONDecodeError:
                pass

        # Check NMEA 2000 custom integration (domain "nmea2000") — normally installed via
        # HACS on Prod, never provisioned on Stage before: without it there is no source
        # of N2K-derived sensors at all, regardless of dashboard/card provisioning above.
        manifest_raw = self.read_config_file("custom_components/nmea2000/manifest.json")
        if manifest_raw:
            try:
                manifest = json.loads(manifest_raw)
                if manifest.get("domain") == "nmea2000":
                    status["nmea2000_integration_installed"] = True
            except json.JSONDecodeError:
                pass

        entries_raw = self.read_config_file(".storage/core.config_entries")
        if entries_raw:
            try:
                data = json.loads(entries_raw)
                entries = data.get("data", {}).get("entries", [])
                if any(e.get("domain") == "nmea2000" for e in entries):
                    status["nmea2000_configured"] = True
                # HACS ACTIVATION (as opposed to delivery of its files above) can only
                # happen through the UI: adding the integration and authorizing via the
                # GitHub device-flow. That is what creates a config entry for domain
                # "hacs" — so this entry, and nothing else, proves activation.
                if any(e.get("domain") == "hacs" for e in entries):
                    status["hacs_activated"] = True
            except json.JSONDecodeError:
                pass

        # Flag clean instance
        if (
            not status["onboarding_done"]
            or not status["dashboard_registered"]
            or status["missing_cards"]
            or not status["hacs_installed"]
            or not status["nmea2000_integration_installed"]
            or not status["nmea2000_configured"]
        ):
            status["is_clean_instance"] = True

        return status

    # ── Provisioning Operations ─────────────────────────────────────────────

    def check_hacs(self) -> Tuple[bool, List[str]]:
        """Separates the two HACS states that are constantly confused:

          * DELIVERED  — custom_components/hacs/manifest.json with domain "hacs" is
            in place. This part is automated (deps.yaml -> fetch_deps.py ->
            deploy_hacs_integration()) and identical on Stage and Prod.
          * ACTIVATED  — a config entry for domain "hacs" exists. This can ONLY be
            done by a human in the UI (Settings -> Add integration -> HACS, then the
            GitHub device-flow at github.com/login/device); it is impossible to
            automate, hence it must be *verified* and used as a hard gate instead.

        Returns (ok, messages_for_the_human).
        """
        status = self.inspect_ha_environment()
        missing: List[str] = []

        if status["hacs_installed"]:
            log("INFO", "HACS files are delivered (custom_components/hacs/manifest.json, domain hacs)")
        else:
            missing.append(
                "HACS is NOT DELIVERED: custom_components/hacs/manifest.json is missing. "
                "This part IS automated — run `python3 helpers/fetch_deps.py` and then "
                f"`./deploy.sh --target {self.profile.name} --bootstrap` "
                "(or `helpers/stage_provisioner.py provision`) to put the pinned HACS release in place."
            )

        if status["hacs_activated"]:
            log("INFO", "HACS is activated (config entry for domain hacs exists)")
        else:
            missing.append(
                "HACS is NOT ACTIVATED: no config entry for domain 'hacs'. This CANNOT be "
                "automated — do it by hand: restart Home Assistant, then "
                f"{self.profile.ha_url or 'the HA UI'} -> Settings -> Devices & services -> "
                "Add integration -> HACS -> authorize at https://github.com/login/device."
            )

        if missing:
            log("ERROR", "HACS check failed:")
            for i, item in enumerate(missing, 1):
                print(f"  {i}. {item}")
            return False, missing

        log("PROVISION", "HACS check passed: delivered AND activated \u2713")
        return True, []

    def provision_onboarding(self) -> bool:
        """Bypasses onboarding wizard by writing completed .storage/onboarding."""
        log("PROVISION", "Bypassing onboarding wizard in .storage/onboarding ...")
        onboarding_data = {
            "version": 1,
            "minor_version": 1,
            "key": "onboarding",
            "data": {
                "done": [
                    "user",
                    "core_config",
                    "analytics",
                    "integration",
                ]
            },
        }
        return self.write_config_file(".storage/onboarding", json.dumps(onboarding_data, indent=2))

    def provision_core_config(self) -> bool:
        """Writes .storage/core.config setting location and unit system."""
        log("PROVISION", "Writing .storage/core.config ...")
        core_config = {
            "version": 1,
            "minor_version": 4,
            "key": "core.config",
            "data": {
                "latitude": 42.43,
                "longitude": 18.60,
                "elevation": 0,
                "radius": 100,
                "unit_system_v2": "metric",
                "location_name": "Sailing Boat",
                "time_zone": "UTC",
                "currency": "EUR",
                "country": "ME",
                "language": "en",
            },
        }
        return self.write_config_file(".storage/core.config", json.dumps(core_config, indent=2))

    def provision_auth(self, username: str = "test", password: str = "test") -> bool:
        """Ensures a real owner user exists with a fixed username/password via the
        standard `homeassistant` auth provider, so Stage HA can always be logged
        into with known credentials (no onboarding registration needed)."""
        log("PROVISION", f"Provisioning owner user '{username}'/'{password}' (homeassistant provider) ...")

        try:
            import bcrypt
        except ImportError:
            log("ERROR", "Python package 'bcrypt' is required for provision_auth() "
                          "(pip install bcrypt / see requirements-ha.txt)")
            return False

        username_norm = username.strip().lower()

        # ── .storage/auth: owner user + credential pointing at the homeassistant provider ──
        auth_raw = self.read_config_file(".storage/auth")
        registry = {
            "version": 1,
            "minor_version": 1,
            "key": "auth",
            "data": {"users": [], "groups": [], "credentials": [], "refresh_tokens": []},
        }
        if auth_raw:
            try:
                registry = json.loads(auth_raw)
            except json.JSONDecodeError:
                pass

        data = registry.setdefault("data", {})
        users = data.setdefault("users", [])
        credentials = data.setdefault("credentials", [])

        owner = next((u for u in users if u.get("is_owner")), None)
        if owner is None:
            owner = {
                "id": uuid.uuid4().hex,
                "group_ids": ["system-admin"],
                "is_owner": True,
                "is_active": True,
                "name": username,
                "system_generated": False,
                "local_only": False,
            }
            users.append(owner)

        # Drop any stale trusted_networks credential from a previous provisioning run.
        credentials[:] = [c for c in credentials if c.get("auth_provider_type") != "trusted_networks"]

        cred = next(
            (c for c in credentials
             if c.get("auth_provider_type") == "homeassistant" and c.get("user_id") == owner["id"]),
            None,
        )
        if cred is None:
            cred = {
                "id": uuid.uuid4().hex,
                "user_id": owner["id"],
                "auth_provider_type": "homeassistant",
                "auth_provider_id": None,
                "data": {"username": username_norm},
            }
            credentials.append(cred)
        else:
            # Keep the credential's embedded username in sync (HA's own
            # homeassistant.async_get_or_create_credentials() reads it from here).
            cred["data"] = {"username": username_norm}

        ok_auth = self.write_config_file(".storage/auth", json.dumps(registry, indent=2))

        # ── .storage/auth_provider.homeassistant: username + bcrypt password hash ──
        provider_raw = self.read_config_file(".storage/auth_provider.homeassistant")
        provider_registry = {
            "version": 1,
            "minor_version": 1,
            "key": "auth_provider.homeassistant",
            "data": {"users": []},
        }
        if provider_raw:
            try:
                provider_registry = json.loads(provider_raw)
            except json.JSONDecodeError:
                pass

        provider_users = provider_registry.setdefault("data", {}).setdefault("users", [])
        password_hash = base64.b64encode(bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12))).decode()

        existing_pu = next((pu for pu in provider_users if pu.get("username") == username_norm), None)
        if existing_pu is None:
            provider_users.append({"username": username_norm, "password": password_hash})
        else:
            existing_pu["password"] = password_hash

        ok_provider = self.write_config_file(
            ".storage/auth_provider.homeassistant", json.dumps(provider_registry, indent=2)
        )

        return ok_auth and ok_provider

    def provision_dashboard_registry(self) -> bool:
        """Registers dashboard-sailing in .storage/lovelace_dashboards."""
        log("PROVISION", "Registering 'dashboard-sailing' in .storage/lovelace_dashboards ...")
        dashboards_raw = self.read_config_file(".storage/lovelace_dashboards")
        registry = {"version": 1, "minor_version": 1, "key": "lovelace_dashboards", "data": {"items": []}}

        if dashboards_raw:
            try:
                registry = json.loads(dashboards_raw)
            except json.JSONDecodeError:
                pass

        items = registry.setdefault("data", {}).setdefault("items", [])
        existing = next((it for it in items if it.get("url_path") == "dashboard-sailing"), None)

        sailing_entry = {
            "id": "sailing",
            "url_path": "dashboard-sailing",
            "title": "Sailing",
            "icon": "mdi:sailing",
            "show_in_sidebar": True,
            "require_admin": False,
            "mode": "storage",
        }

        if existing is None:
            items.append(sailing_entry)
        else:
            existing.update(sailing_entry)

        return self.write_config_file(".storage/lovelace_dashboards", json.dumps(registry, indent=2))

    def provision_resource_registry(self) -> bool:
        """Generates/updates .storage/lovelace_resources with all required card resources."""
        log("PROVISION", "Populating .storage/lovelace_resources registry ...")

        # Load resource paths from yaml if available, or use defaults
        wanted_resources = []
        res_file = RESOURCES_FILE if os.path.isfile(RESOURCES_FILE) else SRC_RESOURCES_FILE
        if os.path.isfile(res_file):
            try:
                import yaml
                with open(res_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    wanted_resources = data.get("resources", [])
            except Exception as e:
                log("WARN", f"Could not load resources YAML {res_file}: {e}")

        if not wanted_resources:
            # Default fallback list of resource URLs
            wanted_resources = [
                {"url": "/local/card-mod.js", "type": "module"},
                {"url": "/local/compass-card.js", "type": "module"},
                {"url": "/local/apexcharts-card.js", "type": "module"},
                {"url": "/local/windrose-card.js?v=2.4.2", "type": "module"},
                {"url": "/local/plotly-graph-card.js?v=1.0.0", "type": "module"},
                {"url": "/local/config-template-card.js?v=1.3.6", "type": "module"},
                {"url": "/local/windy-boat-card.js?v=1.2.0", "type": "module"},
            ]

        # Load existing lovelace_resources from HA
        resources_raw = self.read_config_file(".storage/lovelace_resources")
        registry = {}
        if resources_raw:
            try:
                registry = json.loads(resources_raw)
            except json.JSONDecodeError:
                pass

        # merge_lovelace_resources.merge_registry() is the SINGLE implementation
        # of this merge — deploy.sh runs the very same module as a CLI, so Stage
        # and Prod can never drift apart.
        registry, _ = merge_registry(
            registry, wanted_resources,
            target_env="stage", cards_dir=CARDS_BUILD_DIR, deps_cards_dir=DEPS_CARDS_DIR)

        return self.write_config_file(".storage/lovelace_resources", json.dumps(registry, indent=2))

    def deploy_card_bundles(self) -> bool:
        """Deploys all required custom card JS bundles to /config/www/."""
        log("PROVISION", "Deploying card JS bundles to /config/www/ ...")
        all_ok = True

        for card_filename in ALL_REQUIRED_CARDS:
            # windy-boat-card is ours and is compiled by build.py into build/cards/;
            # the third-party bundles are downloaded by fetch_deps.py into build/deps/cards/.
            src_path = os.path.join(CARDS_BUILD_DIR, card_filename)
            if not os.path.isfile(src_path):
                src_path = os.path.join(DEPS_CARDS_DIR, card_filename)

            if not os.path.isfile(src_path):
                log("WARN", f"Card bundle {card_filename} not found in build/cards nor build/deps/cards "
                            f"— run `python3 fetch_deps.py`")
                all_ok = False
                continue

            # Deploy to /config/www/<filename>
            ok = self.copy_card_to_ha(src_path, f"www/{card_filename}")
            if ok and self.last_copy_skipped:
                log("INFO", f"{card_filename} unchanged in /config/www/ — skipped")
            elif ok:
                log("INFO", f"Deployed {card_filename} -> /config/www/{card_filename}")
            else:
                log("ERROR", f"Failed deploying {card_filename}")
                all_ok = False

            # Also deploy to community subdir if card has a HACS fallback structure
            if card_filename in ["card-mod.js", "compass-card.js", "apexcharts-card.js"]:
                folder_name = card_filename.replace(".js", "")
                if folder_name == "card-mod":
                    folder_name = "lovelace-card-mod"
                comm_path = f"www/community/{folder_name}/{card_filename}"
                self.copy_card_to_ha(src_path, comm_path)

        return all_ok

    @staticmethod
    def fetch_dependency(section: str, dest_dir: str) -> bool:
        """Makes sure one deps.yaml section is present in build/deps/ by delegating
        to fetch_deps.py — the single downloader in this project."""
        if os.path.isfile(os.path.join(dest_dir, "manifest.json")):
            return True
        log("PROVISION", f"Fetching '{section}' from deps.yaml into build/deps/ ...")
        try:
            res = subprocess.run([sys.executable, FETCH_DEPS, "--only", section],
                                 capture_output=True, text=True, timeout=600)
            if res.stdout:
                print(res.stdout.rstrip())
            if res.returncode != 0:
                log("ERROR", res.stderr.strip() or "fetch_deps.py failed")
        except Exception as e:
            log("ERROR", f"Failed running fetch_deps.py: {e}")
            return False
        return os.path.isfile(os.path.join(dest_dir, "manifest.json"))

    def deploy_hacs_integration(self) -> bool:
        """Installs the REAL HACS integration (domain hacs) into
        /config/custom_components/hacs/, the same official release used on Prod
        (not a copy-only emulation of "what HACS would do"). Runs for EVERY profile,
        local-docker and ssh-docker alike, so Stage and Prod never drift apart.

        Delivery only — no config entry is faked here: adding the HACS integration
        and completing the GitHub device-flow in the UI stays a manual step, and
        check_hacs() is what verifies it actually happened."""
        log("PROVISION", "Installing real HACS integration into /config/custom_components/hacs/ ...")
        if not self.fetch_dependency("integrations", HACS_INTEGRATION_DEPS_DIR):
            # NOT a silent warning: HACS delivery is an automated part of the pipeline and
            # its absence blocks every HACS-dependent manual step later on.
            log("ERROR", "HACS release missing from build/deps/ — run `python3 helpers/fetch_deps.py` "
                         "(GitHub must be reachable; the release is pinned in deps.yaml).")
            return False
        return self.copy_dir_to_ha(HACS_INTEGRATION_DEPS_DIR, "custom_components/hacs")

    def deploy_nmea2000_integration(self) -> bool:
        """Installs the 'NMEA 2000' custom integration (domain nmea2000, our own
        dnevera/ha-nmea2000 fork pinned to a tag in deps.yaml) into
        /config/custom_components/nmea2000/."""
        log("PROVISION", "Installing NMEA 2000 custom integration into /config/custom_components/nmea2000/ ...")
        if not self.fetch_dependency("integrations", NMEA2000_INTEGRATION_DEPS_DIR):
            log("ERROR", "NMEA 2000 integration missing from build/deps/ — run `python3 fetch_deps.py`")
            return False
        return self.copy_dir_to_ha(NMEA2000_INTEGRATION_DEPS_DIR, "custom_components/nmea2000")

    def provision_nmea2000_config_entry(
        self, host: Optional[str] = None, port: Optional[int] = None
    ) -> bool:
        """Registers a 'nmea2000' config entry (TEXT/TCP gateway type) pointed at the
        tcp-gw of the selected profile (the local mock_nmea_emulator.py by default),
        so the integration actually connects and starts creating N2K sensor entities
        on startup — without this entry the integration files alone do nothing
        (config_flow is never auto-triggered)."""
        host = host or self.gw_host
        port = port if port is not None else self.gw_port
        log("PROVISION", f"Registering nmea2000 config entry (gateway_type=text, {host}:{port}) ...")
        entries_raw = self.read_config_file(".storage/core.config_entries")
        registry = {"version": 1, "minor_version": 1, "key": "core.config_entries", "data": {"entries": []}}
        if entries_raw:
            try:
                registry = json.loads(entries_raw)
            except json.JSONDecodeError:
                pass

        entries = registry.setdefault("data", {}).setdefault("entries", [])
        existing = next((e for e in entries if e.get("domain") == "nmea2000"), None)

        # TRAP ("Unknown mode 'None' during migration", custom_components/nmea2000/
        # __init__.py:58): the integration's config flow is VERSION = 2, and its
        # async_migrate_entry() only runs for version 1 entries — where it pops the
        # legacy "mode" key and hard-fails when it is absent. An entry we write by
        # hand must therefore be a v2 entry AND still carry the legacy keys, so it
        # survives being migrated by any older/newer variant of the integration.
        entry_data = {
            "name": f"NMEA 2000 ({self.profile.name})",
            "gateway_type": "text",
            "mode": "TCP",          # CONF_MODE_TCP — legacy v1 key, maps to GatewayType.TEXT
            "device_type": "TCP",   # anything but "EBYTE" migrates to TEXT
            "ip": host,
            "port": port,
            "ms_between_updates": 5000,
            "exclude_AIS": False,
        }
        entry_version = 2

        if existing is None:
            entries.append({
                "created_at": "1970-01-01T00:00:00.000000+00:00",
                "modified_at": "1970-01-01T00:00:00.000000+00:00",
                "entry_id": uuid.uuid4().hex,
                "version": entry_version,
                "minor_version": 1,
                "domain": "nmea2000",
                "title": entry_data["name"],
                "data": entry_data,
                "options": {},
                "pref_disable_new_entities": False,
                "pref_disable_polling": False,
                "source": "user",
                "unique_id": None,
                "disabled_by": None,
                "discovery_keys": {},
                # Required by this HA core version's config_entries.async_initialize()
                # (KeyError: 'subentries' on boot without it) — empty dict means "no
                # subentries", same as what a real config-flow-created entry gets.
                "subentries": {},
            })
        else:
            existing["data"].update(entry_data)
            existing["title"] = entry_data["name"]
            # Backfill for entries written by an older version of this script: they
            # were stored as version 1 without "mode", which made the integration's
            # migration log "Unknown mode 'None'" and refuse to load the entry.
            existing["data"].setdefault("mode", "TCP")
            existing["version"] = entry_version
            # Backfill "subentries" on entries created by an older version of this
            # script (before this key was required) — HA core's
            # config_entries.async_initialize() KeyErrors on boot without it.
            existing.setdefault("subentries", {})

        return self.write_config_file(".storage/core.config_entries", json.dumps(registry, indent=2))

    def run_full_provisioning(self) -> bool:
        """Performs full auto-provisioning of clean HA instance."""
        log("PROVISION", "Starting full HA stage auto-provisioning...")
        ok_onboarding = self.provision_onboarding()
        ok_core_cfg = self.provision_core_config()
        ok_auth = self.provision_auth()
        ok_dash = self.provision_dashboard_registry()
        ok_res = self.provision_resource_registry()
        ok_cards = self.deploy_card_bundles()
        ok_hacs = self.deploy_hacs_integration()
        ok_nmea_integration = self.deploy_nmea2000_integration()
        ok_nmea_entry = self.provision_nmea2000_config_entry()

        if not ok_hacs:
            log("ERROR", "HACS files were NOT delivered — the HACS gate will block the install. "
                         "Fix the download and re-run; do not continue as if HACS were optional.")

        success = (
            ok_onboarding and ok_core_cfg and ok_auth and ok_dash and ok_res and ok_cards
            and ok_hacs and ok_nmea_integration and ok_nmea_entry
        )
        if success:
            log("PROVISION", "Full HA stage provisioning completed successfully!")
        else:
            log("WARN", "HA stage provisioning finished with warnings/errors.")
        return success


# ── Post-Launch Verification Helper ─────────────────────────────────────────

def verify_http_readiness(url: str = "http://localhost:8123/dashboard-sailing/", timeout_sec: int = 30) -> bool:
    """Polls target URL until HTTP 200 response or timeout."""
    log("INFO", f"Verifying HTTP readiness at {url} (timeout: {timeout_sec}s)...")
    start_time = time.time()

    while time.time() - start_time < timeout_sec:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HA-Stage-Provisioner"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    log("INFO", f"HTTP 200 OK received from {url}")
                    return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(1.5)

    log("ERROR", f"Timed out waiting for HTTP 200 from {url}")
    return False


# ── CLI Interface ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Home Assistant Stage Environment Provisioner")
    subparsers = parser.add_subparsers(dest="command")

    # Inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect target HA state")
    inspect_parser.add_argument("--config-dir", help="Path to local HA /config directory")
    inspect_parser.add_argument("--container", default=DEFAULT_CONTAINER, help="Target HA container name")
    inspect_parser.add_argument("--target", default=DEFAULT_PROFILE, help="Target profile from .env")

    # Provision command
    provision_parser = subparsers.add_parser("provision", help="Provision target HA instance")
    provision_parser.add_argument("--config-dir", help="Path to local HA /config directory")
    provision_parser.add_argument("--container", help="Target HA container name (default: the profile's)")
    provision_parser.add_argument("--target", default=DEFAULT_PROFILE, help="Target profile from .env")
    provision_parser.add_argument("--clean-install", action="store_true", help="Force clean re-provisioning")
    provision_parser.add_argument(
        "--no-container-cycle", action="store_true",
        help="Do not stop/start the docker container around provisioning (advanced; risks HA "
             "silently overwriting .storage/ edits with its stale in-memory state on next restart)",
    )

    # check-hacs command: the gate between "we delivered the files" and "the human
    # activated it in the UI". Used by install_wizard.sh and deploy.sh --preflight.
    hacs_parser = subparsers.add_parser(
        "check-hacs", help="Check HACS delivery (files) and activation (config entry) separately")
    hacs_parser.add_argument("--config-dir", help="Path to local HA /config directory")
    hacs_parser.add_argument("--container", help="Target HA container name (default: the profile's)")
    hacs_parser.add_argument("--target", default=DEFAULT_PROFILE, help="Target profile from .env")

    # deploy-hacs command: (re)deliver the pinned HACS release into the target,
    # identically for local-docker (Stage) and ssh-docker (Prod) profiles.
    deploy_hacs_parser = subparsers.add_parser(
        "deploy-hacs", help="Deliver the pinned HACS release into the target's custom_components/hacs")
    deploy_hacs_parser.add_argument("--config-dir", help="Path to local HA /config directory")
    deploy_hacs_parser.add_argument("--container", help="Target HA container name (default: the profile's)")
    deploy_hacs_parser.add_argument("--target", default=DEFAULT_PROFILE, help="Target profile from .env")

    # Verify command
    verify_parser = subparsers.add_parser("verify", help="Verify HTTP readiness")
    verify_parser.add_argument("--url", help="Dashboard URL (default: the profile's HA url)")
    verify_parser.add_argument("--target", default=DEFAULT_PROFILE, help="Target profile from .env")
    verify_parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")

    args = parser.parse_args()

    if args.command == "inspect":
        provisioner = HAProvisioner(config_dir=args.config_dir, container_name=args.container,
                                    profile=args.target)
        status = provisioner.inspect_ha_environment()
        print(json.dumps(status, indent=2))
        sys.exit(0 if not status["is_clean_instance"] else 1)

    elif args.command == "provision":
        profile = load_profile(args.target)
        provisioner = HAProvisioner(config_dir=args.config_dir,
                                    container_name=args.container or profile.container,
                                    profile=args.target)

        needs_provisioning = args.clean_install or provisioner.inspect_ha_environment()["is_clean_instance"]

        # IMPORTANT: HA keeps its .storage/ registries loaded in memory and flushes that
        # in-memory state back to disk whenever the container stops/restarts. If we edit
        # .storage/auth, .storage/onboarding, etc. on disk while an already-running
        # container still holds an older/stale copy in memory, the next stop/restart
        # silently overwrites our edits (e.g. a freshly-provisioned test/test login
        # reverts to a broken/empty credential). Stopping the container BEFORE editing
        # and starting it fresh AFTER guarantees HA boots by reading exactly what we wrote.
        did_stop = False
        if needs_provisioning and provisioner.config_dir and not args.no_container_cycle:
            did_stop = provisioner.is_container_running()
            if did_stop and not provisioner.stop_container():
                log("WARN", "Could not stop container before provisioning — continuing anyway, "
                            "but changes may be overwritten on next restart.")

        if args.clean_install:
            log("PROVISION", "Flag --clean-install set: forcing full provisioning")
            success = provisioner.run_full_provisioning()
        elif needs_provisioning:
            log("PROVISION", "Clean HA instance detected: starting auto-provisioning")
            success = provisioner.run_full_provisioning()
        else:
            log("INFO", "HA instance is already initialized. Skipping full provisioning.")
            success = True

        if did_stop:
            provisioner.start_container()

        sys.exit(0 if success else 1)

    elif args.command == "check-hacs":
        profile = load_profile(args.target)
        provisioner = HAProvisioner(config_dir=args.config_dir,
                                    container_name=args.container or profile.container,
                                    profile=args.target)
        ok, _missing = provisioner.check_hacs()
        sys.exit(0 if ok else 1)

    elif args.command == "deploy-hacs":
        profile = load_profile(args.target)
        provisioner = HAProvisioner(config_dir=args.config_dir,
                                    container_name=args.container or profile.container,
                                    profile=args.target)
        sys.exit(0 if provisioner.deploy_hacs_integration() else 1)

    elif args.command == "verify":
        base_url = (load_profile(args.target).ha_url or "http://localhost:8123").rstrip("/")
        url = args.url or f"{base_url}/dashboard-sailing/"
        ok = verify_http_readiness(url=url, timeout_sec=args.timeout)
        sys.exit(0 if ok else 1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
