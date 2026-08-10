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

        # Flag clean instance
        if not status["onboarding_done"] or not status["dashboard_registered"] or status["missing_cards"]:
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

    def run_full_provisioning(self) -> bool:
        """Performs full auto-provisioning of clean HA instance."""
        log("PROVISION", "Starting full HA stage auto-provisioning...")
        ok_onboarding = self.provision_onboarding()
        ok_auth = self.provision_auth()
        ok_dash = self.provision_dashboard_registry()
        ok_res = self.provision_resource_registry()
        ok_cards = self.deploy_card_bundles()

        success = ok_onboarding and ok_auth and ok_dash and ok_res and ok_cards
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
