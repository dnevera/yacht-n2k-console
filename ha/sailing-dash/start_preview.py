#!/usr/bin/env python3
"""
Test Server Launcher for local-preview (ha/sailing-dash)

This script:
1. Audits the build state comparing src/ files against build/ artifacts.
2. Triggers python3 build.py automatically if artifacts are missing or out of date.
3. Verifies local-preview dependencies (vendor JS files).
4. Displays build changes, status summary, and card configuration details.
5. Starts a local HTTP server serving local-preview/ with detailed request logging.
6. Displays instructions in the console for browser testing.
"""

import sys
import os
import glob
import time
import subprocess
import http.server
import socketserver
import urllib.parse
from datetime import datetime

# -----------------------------------------------------------------------------
# Configuration & Paths
# -----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Handle execution from either ha/sailing-dash or ha/sailing-dash/local-preview
if os.path.basename(SCRIPT_DIR) == "local-preview":
    BASE_DIR = os.path.dirname(SCRIPT_DIR)
else:
    BASE_DIR = SCRIPT_DIR

SRC_DIR = os.path.join(BASE_DIR, "src")
BUILD_DIR = os.path.join(BASE_DIR, "build")
PREVIEW_DIR = os.path.join(BASE_DIR, "local-preview")
VENDOR_DIR = os.path.join(PREVIEW_DIR, "vendor")
BUILD_SCRIPT = os.path.join(BASE_DIR, "build.py")

DEFAULT_PORT = 8977

REQUIRED_BUILD_ARTIFACTS = [
    os.path.join(BUILD_DIR, "dashboard-sailing.yaml"),
    os.path.join(BUILD_DIR, "sensors-sailing.yaml"),
    os.path.join(BUILD_DIR, "automations-sailing.yaml"),
    os.path.join(BUILD_DIR, "lovelace-resources.yaml"),
    os.path.join(BUILD_DIR, "cards", "windy-boat-card.js"),
    os.path.join(BUILD_DIR, "local-preview", "card-configs.js"),
]

REQUIRED_VENDOR_FILES = [
    "apexcharts-card.js",
    "compass-card.js",
    "windrose-card.js",
    "plotly-graph-card.js",
    "config-template-card.js",
]

# -----------------------------------------------------------------------------
# Logging Helpers
# -----------------------------------------------------------------------------
def log(level: str, msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix_map = {
        "INFO": "\033[94m[INFO]\033[0m",
        "BUILD": "\033[92m[BUILD]\033[0m",
        "STATUS": "\033[96m[STATUS]\033[0m",
        "WARN": "\033[93m[WARN]\033[0m",
        "ERROR": "\033[91m[ERROR]\033[0m",
        "SERVER": "\033[95m[SERVER]\033[0m",
    }
    prefix = prefix_map.get(level, f"[{level}]")
    print(f"{timestamp} {prefix} {msg}")

# -----------------------------------------------------------------------------
# Build Audit & Execution
# -----------------------------------------------------------------------------
def get_latest_mtime(directory: str) -> float:
    latest = 0.0
    for root, _, files in os.walk(directory):
        for f in files:
            path = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(path)
                if mtime > latest:
                    latest = mtime
            except OSError:
                pass
    return latest

def check_build_status():
    log("INFO", "Auditing project build status...")

    if not os.path.exists(SRC_DIR):
        log("ERROR", f"Source directory missing: {SRC_DIR}")
        sys.exit(1)

    if not os.path.exists(BUILD_SCRIPT):
        log("ERROR", f"Build script missing: {BUILD_SCRIPT}")
        sys.exit(1)

    missing_artifacts = [f for f in REQUIRED_BUILD_ARTIFACTS if not os.path.exists(f)]
    src_latest_mtime = get_latest_mtime(SRC_DIR)

    build_mtime = 0.0
    if os.path.exists(BUILD_DIR):
        for f in REQUIRED_BUILD_ARTIFACTS:
            if os.path.exists(f):
                t = os.path.getmtime(f)
                if build_mtime == 0.0 or t < build_mtime:
                    build_mtime = t  # min mtime of artifacts

    rebuild_needed = False
    reason = ""

    if missing_artifacts:
        rebuild_needed = True
        reason = f"Missing {len(missing_artifacts)} build artifact(s)"
    elif src_latest_mtime > build_mtime:
        rebuild_needed = True
        src_time_str = datetime.fromtimestamp(src_latest_mtime).strftime("%Y-%m-%d %H:%M:%S")
        build_time_str = datetime.fromtimestamp(build_mtime).strftime("%Y-%m-%d %H:%M:%S")
        reason = f"Source files updated ({src_time_str}) after build artifacts ({build_time_str})"

    if rebuild_needed:
        log("BUILD", f"Rebuild required: {reason}")
        log("BUILD", f"Executing build script: python3 {os.path.basename(BUILD_SCRIPT)}...")
        try:
            start_time = time.time()
            result = subprocess.run(
                [sys.executable, BUILD_SCRIPT],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                check=True
            )
            elapsed = time.time() - start_time
            log("BUILD", f"Build completed successfully in {elapsed:.2f}s!")
            if result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    log("BUILD", f"  {line}")
        except subprocess.CalledProcessError as e:
            log("ERROR", f"Build failed with exit code {e.returncode}:")
            if e.stderr:
                print(e.stderr)
            if e.stdout:
                print(e.stdout)
            sys.exit(1)
    else:
        log("INFO", "Build is up-to-date. No rebuild necessary.")

def check_vendor_files():
    log("INFO", "Checking vendor JS dependencies in local-preview/vendor/...")
    missing_or_small = []
    for vf in REQUIRED_VENDOR_FILES:
        path = os.path.join(VENDOR_DIR, vf)
        if not os.path.exists(path):
            missing_or_small.append((vf, "missing"))
        elif os.path.getsize(path) < 1000:
            missing_or_small.append((vf, f"too small ({os.path.getsize(path)} bytes)"))

    if missing_or_small:
        log("WARN", "Some vendor JS files are missing or incomplete:")
        for vf, status in missing_or_small:
            log("WARN", f"  - {vf}: {status}")
        log("WARN", f"Run 'bash {os.path.join(PREVIEW_DIR, 'fetch-vendor.sh')}' to download vendor bundles.")
    else:
        log("INFO", f"All {len(REQUIRED_VENDOR_FILES)} vendor JS files verified in vendor/.")

def report_changes_and_summary():
    log("STATUS", "--- Build Artifacts & Configuration Summary ---")
    for f in REQUIRED_BUILD_ARTIFACTS:
        rel_path = os.path.relpath(f, BASE_DIR)
        size = os.path.getsize(f) if os.path.exists(f) else 0
        mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%H:%M:%S") if os.path.exists(f) else "N/A"
        log("STATUS", f"  ✓ {rel_path:<35} ({size:>6} bytes, updated {mtime})")

    # Inspect card-configs.js to count preview cards
    card_configs_path = os.path.join(BUILD_DIR, "local-preview", "card-configs.js")
    card_count = 0
    if os.path.exists(card_configs_path):
        try:
            with open(card_configs_path, "r", encoding="utf-8") as file:
                content = file.read()
                card_count = content.count('"tag":') + content.count("'tag':") + content.count("tag:")
        except Exception:
            pass
    log("STATUS", f"Preview harness configured with {card_count} Lovelace card(s).")

# -----------------------------------------------------------------------------
# HTTP Server Request Handler
# -----------------------------------------------------------------------------
class LoggingHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PREVIEW_DIR, **kwargs)

    def log_message(self, format_str, *args):
        # Detailed request logging
        msg = format_str % args
        log("SERVER", f"{self.address_string()} - {msg}")

    def end_headers(self):
        # Prevent caching for live preview edits
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

# -----------------------------------------------------------------------------
# Server Runner
# -----------------------------------------------------------------------------
def start_preview_server(port=DEFAULT_PORT):
    check_build_status()
    check_vendor_files()
    report_changes_and_summary()

    # Verify cards symlink in local-preview/
    symlink_path = os.path.join(PREVIEW_DIR, "cards")
    if not os.path.exists(symlink_path):
        target = os.path.join("..", "build", "cards")
        try:
            os.symlink(target, symlink_path)
            log("INFO", f"Created symlink: local-preview/cards -> {target}")
        except Exception as e:
            log("WARN", f"Could not create cards symlink: {e}")

    actual_port = port
    handler = LoggingHTTPRequestHandler
    
    # Attempt port binding with fallback
    server = None
    for attempt_port in range(port, port + 10):
        try:
            server = socketserver.TCPServer(("", attempt_port), handler)
            actual_port = attempt_port
            break
        except OSError:
            log("WARN", f"Port {attempt_port} is currently in use, trying next port...")

    if not server:
        log("ERROR", f"Could not bind to any port in range {port}-{port + 9}.")
        sys.exit(1)

    url = f"http://localhost:{actual_port}/index.html"
    alt_url = f"http://127.0.0.1:{actual_port}/index.html"

    print("\n" + "=" * 74)
    print(" 🚀 LOCAL PREVIEW TEST SERVER IS READY & RUNNING!")
    print("=" * 74)
    print(f"\n  📍 Preview URL:     \033[1;32m{url}\033[0m")
    print(f"  📍 Alternative URL: \033[1;32m{alt_url}\033[0m\n")
    print("  👉 WHAT TO DO NEXT:")
    print("     1. Click or open the URL in your web browser:")
    print(f"        \033[4;36m{url}\033[0m")
    print("     2. Review custom card rendering (ApexCharts, Windrose, Plotly, etc.).")
    print("     3. Check the browser console (F12) for card errors or warnings.")
    print("     4. Press Ctrl+C in this terminal window anytime to stop the server.\n")
    print("=" * 74 + "\n")

    log("SERVER", f"Serving HTTP on port {actual_port} from {PREVIEW_DIR}...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        log("INFO", "Server stop requested by user (Ctrl+C). Cleaning up...")
    finally:
        server.server_close()
        log("INFO", "Preview server stopped.")

if __name__ == "__main__":
    port_arg = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port_arg = int(sys.argv[1])
        except ValueError:
            pass
    start_preview_server(port_arg)
