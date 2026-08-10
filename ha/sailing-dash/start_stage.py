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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
LOCAL_HA_DIR = os.path.join(SCRIPT_DIR, "local-ha")
SRC_DIR = os.path.join(SCRIPT_DIR, "src")
BUILD_SCRIPT = os.path.join(SCRIPT_DIR, "build.py")
DEPLOY_SCRIPT = os.path.join(SCRIPT_DIR, "deploy.sh")
EMULATOR_SCRIPT = os.path.join(LOCAL_HA_DIR, "mock_nmea_emulator.py")


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
    log("BUILD", "Compiling modules via build.py ...")
    res_build = subprocess.run([sys.executable, BUILD_SCRIPT], cwd=SCRIPT_DIR)
    if res_build.returncode != 0:
        log("ERROR", "Build failed! Fix errors in src/ modules.")
        return False

    log("STAGE", "Deploying build artifacts to local Stage HA container ...")
    deploy_cmd = ["bash", DEPLOY_SCRIPT, "--stage"]
    if clean_install:
        deploy_cmd.append("--clean-install")
    res_deploy = subprocess.run(deploy_cmd, cwd=SCRIPT_DIR)
    if res_deploy.returncode != 0:
        log("WARN", "Deploy to local-ha returned non-zero code.")
        return False

    return True


def start_docker_stage():
    log("STAGE", "Building and starting local-ha Docker container ...")
    cmd = ["docker", "compose", "up", "-d", "--build"]
    res = subprocess.run(cmd, cwd=LOCAL_HA_DIR)
    if res.returncode != 0:
        log("ERROR", "Failed to build/start local-ha container via docker compose.")
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
    parser.add_argument("--gw-host", default="", help="Remote NMEA TCP gateway host for --live mode")
    parser.add_argument("--clean-install", "--install", action="store_true", help="Force clean re-provisioning of Stage HA")
    parser.add_argument("--no-watch", action="store_true", help="Disable file watcher")
    args = parser.parse_args()

    mode_str = "LIVE" if args.live else "DEMO"

    check_docker()
    start_docker_stage()

    emulator_proc = None
    if not args.live:
        log("STAGE", "Starting background NMEA PGN simulator on port 4001 ...")
        emulator_proc = subprocess.Popen([sys.executable, EMULATOR_SCRIPT, "--port", "4001"], cwd=LOCAL_HA_DIR)

    run_build_and_deploy(clean_install=args.clean_install)

    log("STAGE", "Verifying Stage HA dashboard HTTP readiness...")
    verify_cmd = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "stage_provisioner.py"),
        "verify",
        "--url",
        "http://localhost:8123/dashboard-sailing/",
        "--timeout",
        "15",
    ]
    subprocess.run(verify_cmd, cwd=SCRIPT_DIR)

    print("\n" + "=" * 70)
    print("🚀 Stage Home Assistant Environment Ready!")
    print("=" * 70)
    print("📌 Dashboard URL:  http://localhost:8123/dashboard-sailing/")
    print(f"📡 NMEA 2000 Mode: {mode_str} (" + ("Local PGN Simulator on :4001" if not args.live else f"Gateway: {args.gw-host or 'deploy.conf'}") + ")")
    print("🐳 Container:      local-ha")
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
