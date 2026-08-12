#!/usr/bin/env python3
"""
Stage Environment Orchestrator for Home Assistant (ha/sailing-dash)

Automates:
1. Initializing and running the local Stage Home Assistant Docker container (local-ha).
2. Running the NMEA 2000 telemetry provider:
   - --demo (default): Launches local Python NMEA PGN simulator (mock_nmea_emulator.py on :4001).
   - --live: Connects Stage HA to live remote NMEA TCP gateway (:4001 on Pi5).
3. Automatic build (build.py) and deployment (deploy.sh --stage).
4. Live file watcher on src/ auto-rebuilding and re-deploying on changes.
"""

import sys
import os
import time
import subprocess
import threading
import argparse
from datetime import datetime

from env_profile import load_profile

# This script lives in ha/sailing-dash/helpers/; local-ha/, src/ and deploy.sh
# belong to the subproject root one level up.
HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(HELPERS_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
LOCAL_HA_DIR = os.path.join(SCRIPT_DIR, "local-ha")
SRC_DIR = os.path.join(SCRIPT_DIR, "src")
DEPLOY_SCRIPT = os.path.join(SCRIPT_DIR, "deploy.sh")
EMULATOR_SCRIPT = os.path.join(LOCAL_HA_DIR, "mock_nmea_emulator.py")

# The Stage target is a NAMED PROFILE from .env (see .env.template): the bundled
# local-ha compose stack is only the default of the "stage" profile, and --target
# picks any other one (e.g. a stage container living on another Pi5).
STAGE_PROFILE = load_profile(os.environ.get("HA_PROFILE") or "stage")
STAGE_CONTAINER = os.environ.get("HA_CONTAINER") or STAGE_PROFILE.container


def log(level: str, msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix_map = {
        "INFO": "\033[94m[INFO]\033[0m",
        "STAGE": "\033[92m[STAGE]\033[0m",
        "BUILD": "\033[96m[BUILD]\033[0m",
        "WARN": "\033[93m[WARN]\033[0m",
        "ERROR": "\033[91m[ERROR]\033[0m",
    }
    prefix = prefix_map.get(level, f"[{level}]")
    print(f"{timestamp} {prefix} {msg}")


def check_docker():
    try:
        res = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            log("ERROR", "Docker daemon is not running. Please start Docker and try again.")
            sys.exit(1)
    except FileNotFoundError:
        log("ERROR", "Docker CLI not found in PATH. Please install Docker.")
        sys.exit(1)


def run_build_and_deploy(clean_install: bool = False):
    # build.py is NOT called here: deploy.sh is the single entry point and runs it
    # once per pipeline (it also fetches deps.yaml artifacts before deploying).
    log("STAGE", f"Building and deploying artifacts to Stage HA container '{STAGE_CONTAINER}' ...")
    deploy_cmd = ["bash", DEPLOY_SCRIPT, "--target", STAGE_PROFILE.name]
    if clean_install:
        deploy_cmd.append("--clean-install")
    res_deploy = subprocess.run(deploy_cmd, cwd=SCRIPT_DIR)
    if res_deploy.returncode != 0:
        log("WARN", f"Deploy to {STAGE_CONTAINER} returned non-zero code.")
        return False

    return True


def start_docker_stage():
    log("STAGE", f"Building and starting the Stage Docker container '{STAGE_CONTAINER}' ...")
    cmd = ["docker", "compose", "up", "-d", "--build"]
    res = subprocess.run(cmd, cwd=LOCAL_HA_DIR)
    if res.returncode != 0:
        log("ERROR", "Failed to build/start the Stage container via docker compose.")
        sys.exit(1)


def get_src_mtime():
    latest = 0.0
    for root, _, files in os.walk(SRC_DIR):
        for f in files:
            p = os.path.join(root, f)
            try:
                t = os.path.getmtime(p)
                if t > latest:
                    latest = t
            except OSError:
                pass
    return latest


def start_file_watcher():
    last_mtime = get_src_mtime()
    while True:
        time.sleep(1.5)
        current_mtime = get_src_mtime()
        if current_mtime > last_mtime:
            log("INFO", "Source file change detected in src/ -> trigger auto re-build & deploy...")
            last_mtime = current_mtime
            run_build_and_deploy()


def main():
    parser = argparse.ArgumentParser(description="Stage Home Assistant Environment Launcher")
    parser.add_argument("--demo", action="store_true", default=True, help="Demo mode: run local NMEA PGN simulator (default)")
    parser.add_argument("--live", action="store_true", help="Live mode: connect Stage HA to remote NMEA TCP gateway")
    parser.add_argument("--target", default=None, help="Target profile from .env (default: stage)")
    parser.add_argument("--gw-host", default="", help="YDNU-02 tcp-gw host (default: the profile's GW_HOST)")
    parser.add_argument("--gw-port", type=int, default=0, help="tcp-gw data port (default: the profile's GW_DATA_PORT)")
    parser.add_argument("--clean-install", "--install", action="store_true", help="Force clean re-provisioning of Stage HA")
    parser.add_argument("--no-watch", action="store_true", help="Disable file watcher")
    parser.add_argument(
        "--provision-only", action="store_true",
        help="Bring the container up, start the emulator (demo) and provision HA, then EXIT "
             "without deploying sensors/dashboard. Used by install_wizard.sh, whose HACS and "
             "NMEA-2000 gates must be passed before anything is deployed.",
    )
    args = parser.parse_args()

    if args.target:
        global STAGE_PROFILE, STAGE_CONTAINER
        STAGE_PROFILE = load_profile(args.target)
        STAGE_CONTAINER = os.environ.get("HA_CONTAINER") or STAGE_PROFILE.container

    gw_host = args.gw_host or STAGE_PROFILE.gw_host
    gw_port = args.gw_port or STAGE_PROFILE.gw_data_port
    # Propagate an explicit gateway override to everything spawned below
    # (deploy.sh -> stage_provisioner.py) the same way .env does it: as the
    # profile-prefixed environment variables, which always win over .env.
    prefix = STAGE_PROFILE.name.upper().replace("-", "_")
    os.environ[f"{prefix}_GW_HOST"] = str(gw_host)
    os.environ[f"{prefix}_GW_DATA_PORT"] = str(gw_port)
    mode_str = "LIVE" if args.live else "DEMO"

    check_docker()
    start_docker_stage()

    emulator_proc = None
    if not args.live:
        log("STAGE", f"Starting background NMEA PGN simulator on port {gw_port} ...")
        emulator_proc = subprocess.Popen(
            [sys.executable, EMULATOR_SCRIPT, "--port", str(gw_port)], cwd=LOCAL_HA_DIR,
            # In --provision-only we exit right after provisioning, but the emulator must
            # keep feeding the bus so raw nmea2000 entities can appear while the operator
            # works through the wizard's manual gates.
            start_new_session=args.provision_only,
        )

    if args.provision_only:
        provision_cmd = [
            sys.executable, os.path.join(HELPERS_DIR, "stage_provisioner.py"), "provision",
            "--target", STAGE_PROFILE.name, "--container", STAGE_CONTAINER,
        ]
        if args.clean_install:
            provision_cmd.append("--clean-install")
        log("STAGE", "Provisioning only (no deploy): the wizard deploys after its gates pass.")
        rc = subprocess.run(provision_cmd, cwd=SCRIPT_DIR).returncode
        if emulator_proc:
            log("STAGE", f"NMEA emulator left running in the background (pid {emulator_proc.pid}, "
                         f"port {gw_port}). Stop it with: kill {emulator_proc.pid}")
        sys.exit(rc)

    run_build_and_deploy(clean_install=args.clean_install)

    log("STAGE", "Verifying Stage HA dashboard HTTP readiness...")
    dashboard_url = (STAGE_PROFILE.ha_url or "http://localhost:8123").rstrip("/") + "/dashboard-sailing/"
    verify_cmd = [
        sys.executable,
        os.path.join(HELPERS_DIR, "stage_provisioner.py"),
        "verify",
        "--target",
        STAGE_PROFILE.name,
        "--timeout",
        "15",
    ]
    subprocess.run(verify_cmd, cwd=SCRIPT_DIR)

    print("\n" + "=" * 70)
    print("🚀 Stage Home Assistant Environment Ready!")
    print("=" * 70)
    print(f"📌 Dashboard URL:  {dashboard_url}")
    print(f"📡 NMEA 2000 Mode: {mode_str} (" + (f"Local PGN Simulator on :{gw_port}" if not args.live else f"Gateway: {gw_host}:{gw_port}") + ")")
    print(f"🐳 Target:         profile {STAGE_PROFILE.name}, container {STAGE_CONTAINER}")
    print("👀 File Watcher:   Active (monitoring src/ for auto build & deploy)")
    print("=" * 70 + "\n")

    if not args.no_watch:
        watcher_thread = threading.Thread(target=start_file_watcher, daemon=True)
        watcher_thread.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[STAGE] Shutting down Stage environment...")
        if emulator_proc:
            emulator_proc.terminate()
            emulator_proc.wait()


if __name__ == "__main__":
    main()
