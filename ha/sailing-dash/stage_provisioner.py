#!/usr/bin/env python3
"""
Home Assistant Stage Provisioner Engine (ha/sailing-dash/stage_provisioner.py)

Handles Home Assistant state inspection, onboarding bypass, .storage registry
initialization (dashboards & resources), card bundle deployment, and post-launch
HTTP readiness checks.
"""

import os
import sys
import json
import uuid
import time
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
VENDOR_DIR = os.path.join(SCRIPT_DIR, "vendor")
CARDS_BUILD_DIR = os.path.join(SCRIPT_DIR, "build", "cards")
RESOURCES_FILE = os.path.join(SCRIPT_DIR, "build", "lovelace-resources.yaml")
SRC_RESOURCES_FILE = os.path.join(SCRIPT_DIR, "src", "yaml", "resources", "lovelace-resources.yaml")
LOCAL_CONFIG_DIR = os.path.join(SCRIPT_DIR, "local-ha", "config")

# The "NMEA 2000" HA custom integration (domain "nmea2000") is normally installed on
# Prod (bumblebee.local) manually through HACS — it is NOT one of the 3 HACS *frontend
# card* resources tracked in ALL_REQUIRED_CARDS/requirements-ha.txt, and Stage never had
# it at all: no HACS, no /config/custom_components/nmea2000, no config entry. Without it
# there is no source of N2K-derived sensors on Stage, no matter how well the dashboard/
# cards are provisioned — this vendored copy is mirrored from OUR OWN fork
# github.com/dnevera/ha-nmea2000 (branch bumblebee-custom, based on upstream
# tomer-w/ha-nmea2000), whose manifest.json already points at our own
# dnevera/nmea2000 library fork. Copying these files directly (HACS itself is
# just a downloader/updater) has the identical effect at runtime as installing
# through HACS on Prod.
NMEA2000_INTEGRATION_VENDOR_DIR = os.path.join(VENDOR_DIR, "custom_components", "nmea2000")
NMEA2000_EMULATOR_HOST = "127.0.0.1"  # local-ha uses docker network_mode: host
NMEA2000_EMULATOR_PORT = 4001         # mock_nmea_emulator.py default port

# HACS (Home Assistant Community Store) itself — Stage used to have NO HACS at all,
# with the nmea2000 integration/frontend cards above installed by directly copying
# vendored files instead ("HACS is just a downloader"). Per explicit request, Stage
# now installs the REAL HACS integration (same official release used on Prod), not a
# copy-only emulation, so `local-ha` looks/behaves like the real bumblebee.local
# instance (HACS panel, updates, custom repositories UI). The release .zip is heavy
# (~50MB, mostly the prebuilt hacs_frontend/ JS bundle) so it is NOT vendored/committed
# into git like the small card bundles — it is downloaded once from GitHub and cached
# under .cache/hacs/ (gitignored), exactly like HACS's own real-world install script
# (https://get.hacs.xz/download) does on Prod.
HACS_RELEASE_URL = "https://github.com/hacs/integration/releases/latest/download/hacs.zip"
HACS_CACHE_DIR = os.path.join(SCRIPT_DIR, ".cache", "hacs")
HACS_CACHE_ZIP = os.path.join(HACS_CACHE_DIR, "hacs.zip")
HACS_CACHE_EXTRACTED_DIR = os.path.join(HACS_CACHE_DIR, "custom_components", "hacs")

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

    def __init__(self, config_dir: Optional[str] = None, container_name: str = "local-ha"):
        self.config_dir = os.path.abspath(config_dir) if config_dir else None
        self.container_name = container_name

        # Fallback to local-ha/config if it exists and config_dir not explicitly passed
        if not self.config_dir and os.path.isdir(LOCAL_CONFIG_DIR):
            self.config_dir = LOCAL_CONFIG_DIR

    # ── Container / File Operations ──────────────────────────────────────────

    def _exec_docker(self, cmd: List[str]) -> Tuple[int, str]:
        """Runs docker exec in target container."""
        full_cmd = ["docker", "exec", self.container_name] + cmd
        try:
            res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=10)
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

    def copy_card_to_ha(self, src_file: str, dest_rel_path: str) -> bool:
        """Copies a local JS card file to /config/www/... inside HA."""
        if not os.path.isfile(src_file):
            return False

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

        # Convert /hacsfiles/ to /local/ for standalone/clean stage setup
        normalized_wanted = []
        for r in wanted_resources:
            url = r.get("url", "")
            rtype = r.get("type", "module")
            if url.startswith("/hacsfiles/"):
                card_name = url.rsplit("/", 1)[-1]
                normalized_wanted.append({"url": f"/local/{card_name}", "type": rtype})
            else:
                normalized_wanted.append({"url": url, "type": rtype})

        # Load existing lovelace_resources from HA
        resources_raw = self.read_config_file(".storage/lovelace_resources")
        registry = {"version": 1, "minor_version": 1, "key": "lovelace_resources", "data": {"items": []}}
        if resources_raw:
            try:
                registry = json.loads(resources_raw)
            except json.JSONDecodeError:
                pass

        items = registry.setdefault("data", {}).setdefault("items", [])

        def clean_url(u: str) -> str:
            return urllib.parse.urlsplit(u).path

        existing_paths = {clean_url(it.get("url", "")): it for it in items}

        for entry in normalized_wanted:
            url = entry["url"]
            path = clean_url(url)
            if path in existing_paths:
                existing_paths[path]["url"] = url
                existing_paths[path]["type"] = entry.get("type", "module")
            else:
                items.append({
                    "id": uuid.uuid4().hex[:24],
                    "url": url,
                    "type": entry.get("type", "module"),
                })

        return self.write_config_file(".storage/lovelace_resources", json.dumps(registry, indent=2))

    def deploy_card_bundles(self) -> bool:
        """Deploys all required custom card JS bundles to /config/www/."""
        log("PROVISION", "Deploying card JS bundles to /config/www/ ...")
        all_ok = True

        for card_filename in ALL_REQUIRED_CARDS:
            # Check build/cards first, then vendor/
            src_path = os.path.join(CARDS_BUILD_DIR, card_filename)
            if not os.path.isfile(src_path):
                src_path = os.path.join(VENDOR_DIR, card_filename)

            if not os.path.isfile(src_path):
                log("WARN", f"Card bundle {card_filename} not found in build/cards nor vendor/")
                all_ok = False
                continue

            # Deploy to /config/www/<filename>
            ok = self.copy_card_to_ha(src_path, f"www/{card_filename}")
            if ok:
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

    def download_hacs_release(self) -> bool:
        """Downloads the official HACS release .zip from GitHub (cached under
        .cache/hacs/, gitignored) and extracts custom_components/hacs/ from it,
        mirroring what HACS's own real-world install script does on Prod."""
        if os.path.isfile(os.path.join(HACS_CACHE_EXTRACTED_DIR, "manifest.json")):
            return True
        log("PROVISION", f"Downloading HACS release from {HACS_RELEASE_URL} (cached under .cache/hacs/) ...")
        os.makedirs(HACS_CACHE_DIR, exist_ok=True)
        try:
            req = urllib.request.Request(HACS_RELEASE_URL, headers={"User-Agent": "HA-Stage-Provisioner"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(HACS_CACHE_ZIP, "wb") as f:
                shutil.copyfileobj(resp, f)
            extract_root = os.path.join(HACS_CACHE_DIR, "custom_components", "hacs")
            os.makedirs(extract_root, exist_ok=True)
            with zipfile.ZipFile(HACS_CACHE_ZIP) as zf:
                zf.extractall(extract_root)
            return os.path.isfile(os.path.join(extract_root, "manifest.json"))
        except Exception as e:
            log("ERROR", f"Failed downloading/extracting HACS release: {e}")
            return False

    def deploy_hacs_integration(self) -> bool:
        """Installs the REAL HACS integration (domain hacs) into
        /config/custom_components/hacs/, the same official release used on Prod
        (not a copy-only emulation of "what HACS would do"). Also registers a
        stub core.config_entries 'hacs' entry so the panel appears immediately;
        completing the GitHub device-flow login still requires opening the HA UI
        once, same as a fresh Prod install."""
        log("PROVISION", "Installing real HACS integration into /config/custom_components/hacs/ ...")
        if not self.download_hacs_release():
            log("WARN", "HACS release unavailable (no network?) — skipping HACS install for this run.")
            return False
        return self.copy_dir_to_ha(HACS_CACHE_EXTRACTED_DIR, "custom_components/hacs")

    def deploy_nmea2000_integration(self) -> bool:
        """Installs the 'NMEA 2000' custom integration (domain nmea2000) into
        /config/custom_components/nmea2000/. On Prod this is normally installed
        manually through HACS from OUR OWN fork github.com/dnevera/ha-nmea2000
        (branch bumblebee-custom); Stage never had it, HACS or not — copying
        the same vendored files has an identical runtime effect (HACS itself
        is just a downloader, not a requirement of the integration itself).
        HA installs the manifest's pip requirement (our patched git fork,
        same as requirements.txt) automatically on startup when it discovers
        a config entry for this domain."""
        log("PROVISION", "Installing NMEA 2000 custom integration into /config/custom_components/nmea2000/ ...")
        if not os.path.isdir(NMEA2000_INTEGRATION_VENDOR_DIR):
            log("ERROR", f"Vendored NMEA 2000 integration not found at {NMEA2000_INTEGRATION_VENDOR_DIR}")
            return False
        return self.copy_dir_to_ha(NMEA2000_INTEGRATION_VENDOR_DIR, "custom_components/nmea2000")

    def provision_nmea2000_config_entry(
        self, host: str = NMEA2000_EMULATOR_HOST, port: int = NMEA2000_EMULATOR_PORT
    ) -> bool:
        """Registers a 'nmea2000' config entry (TEXT/TCP gateway type) pointed at
        the local mock_nmea_emulator.py, so the integration actually connects and
        starts creating N2K sensor entities on startup — without this entry the
        integration files alone do nothing (config_flow is never auto-triggered)."""
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

        entry_data = {
            "name": "Stage NMEA 2000 Emulator",
            "gateway_type": "text",
            "ip": host,
            "port": port,
            "ms_between_updates": 5000,
            "exclude_AIS": True,
        }

        if existing is None:
            entries.append({
                "created_at": "1970-01-01T00:00:00.000000+00:00",
                "modified_at": "1970-01-01T00:00:00.000000+00:00",
                "entry_id": uuid.uuid4().hex,
                "version": 1,
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
            # Backfill "subentries" on entries created by an older version of this
            # script (before this key was required) — HA core's
            # config_entries.async_initialize() KeyErrors on boot without it.
            existing.setdefault("subentries", {})

        return self.write_config_file(".storage/core.config_entries", json.dumps(registry, indent=2))

    def run_full_provisioning(self) -> bool:
        """Performs full auto-provisioning of clean HA instance."""
        log("PROVISION", "Starting full HA stage auto-provisioning...")
        ok_onboarding = self.provision_onboarding()
        ok_auth = self.provision_auth()
        ok_dash = self.provision_dashboard_registry()
        ok_res = self.provision_resource_registry()
        ok_cards = self.deploy_card_bundles()
        ok_hacs = self.deploy_hacs_integration()
        ok_nmea_integration = self.deploy_nmea2000_integration()
        ok_nmea_entry = self.provision_nmea2000_config_entry()

        if not ok_hacs:
            log("WARN", "HACS install skipped/failed (likely no network access) — continuing without it; "
                        "the NMEA 2000 integration/cards above are still installed directly regardless of HACS.")

        success = (
            ok_onboarding and ok_auth and ok_dash and ok_res and ok_cards
            and ok_nmea_integration and ok_nmea_entry
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
    inspect_parser.add_argument("--container", default="local-ha", help="Target HA container name")

    # Provision command
    provision_parser = subparsers.add_parser("provision", help="Provision target HA instance")
    provision_parser.add_argument("--config-dir", help="Path to local HA /config directory")
    provision_parser.add_argument("--container", default="local-ha", help="Target HA container name")
    provision_parser.add_argument("--clean-install", action="store_true", help="Force clean re-provisioning")
    provision_parser.add_argument(
        "--no-container-cycle", action="store_true",
        help="Do not stop/start the docker container around provisioning (advanced; risks HA "
             "silently overwriting .storage/ edits with its stale in-memory state on next restart)",
    )

    # Verify command
    verify_parser = subparsers.add_parser("verify", help="Verify HTTP readiness")
    verify_parser.add_argument("--url", default="http://localhost:8123/dashboard-sailing/", help="Dashboard URL")
    verify_parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")

    args = parser.parse_args()

    if args.command == "inspect":
        provisioner = HAProvisioner(config_dir=args.config_dir, container_name=args.container)
        status = provisioner.inspect_ha_environment()
        print(json.dumps(status, indent=2))
        sys.exit(0 if not status["is_clean_instance"] else 1)

    elif args.command == "provision":
        provisioner = HAProvisioner(config_dir=args.config_dir, container_name=args.container)

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

    elif args.command == "verify":
        ok = verify_http_readiness(url=args.url, timeout_sec=args.timeout)
        sys.exit(0 if ok else 1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
