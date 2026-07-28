#!/usr/bin/env python3
"""
YDNU-02 API endpoint tests — data-driven.
Run: python3 test_api.py [base_url]
"""
import sys
import json
import time
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.68.56:8080"


# === Test runner ===

def run_test(spec):
    """Execute a single test spec and return (pass, label, detail)."""
    method = spec.get("method", "GET")
    path = spec["path"]
    body = spec.get("body")
    expect_keys = spec.get("keys", [])
    expect_status = spec.get("status", 200)
    timeout = spec.get("timeout", 30)
    label = f"{method} {path}"

    url = BASE + path
    delay = spec.get("_delay", 0)
    if delay:
        time.sleep(delay)
    try:
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        if body:
            req.add_header("Content-Type", "application/json")

        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - t0
        raw = resp.read().decode()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = raw

        # Validate keys
        missing = [k for k in expect_keys if isinstance(result, dict) and k not in result]
        if missing:
            return False, label, f"{elapsed:.1f}s  missing keys: {missing}"

        detail = f"{elapsed:.1f}s"
        if isinstance(result, dict):
            for k, v in result.items():
                sv = str(v)[:60] + ("..." if len(str(v)) > 60 else "")
                detail += f"\n    {k}: {sv}"
        else:
            detail += f"  len={len(result)}"
        return True, label, detail

    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode()[:80]
        except Exception:
            pass
        if e.code == expect_status:
            return True, label, f"HTTP {e.code} (expected): {body_text}"
        return False, label, f"HTTP {e.code} (expected {expect_status}): {body_text}"

    except Exception as e:
        return False, label, f"{type(e).__name__}: {e}"


# === Test definitions ===

TESTS = [
    # --- Device Info & Sensors ---
    {"name": "Info (cached)",   "path": "/api/info",             "keys": ["firmware_version", "serial_number", "state", "port"]},
    {"name": "Info (force)",    "path": "/api/info?force=true",  "keys": ["firmware_version", "state"]},
    {"name": "Sensors Live",    "path": "/api/sensors",          "keys": ["status", "fluid_levels", "count"]},

    # --- Mode ---
    {"name": "Mode AUTO",       "method": "POST", "path": "/api/mode/auto",  "keys": ["status", "message"]},
    {"name": "Mode RAW",        "method": "POST", "path": "/api/mode/raw",   "keys": ["status", "message"]},
    {"name": "Mode INVALID",    "method": "POST", "path": "/api/mode/xxx",   "status": 400},

    # --- Silent ---
    {"name": "Silent ON",       "method": "POST", "path": "/api/silent/on",  "keys": ["status", "message"]},
    {"name": "Silent OFF",      "method": "POST", "path": "/api/silent/off", "keys": ["status", "message"]},

    # --- Backups ---
    {"name": "List Backups",    "path": "/api/backups",            "keys": ["backups"]},
    {"name": "Create Backup",   "method": "POST", "path": "/api/backup", "keys": ["status", "filepath"], "timeout": 60},
    {"name": "Verify Backups",  "path": "/api/backups",            "keys": ["backups"]},

    # --- Service ops ---
    {"name": "Filters",         "path": "/api/filters",            "keys": ["filters"],      "timeout": 60},
    {"name": "Settings",        "path": "/api/settings",           "keys": ["settings_raw"], "timeout": 45},
    {"name": "Diag ALL",        "path": "/api/diag/ALL",           "keys": ["data"],         "timeout": 45},
    {"name": "Service CMD",     "method": "POST", "path": "/api/service/cmd", "body": {"cmd": "HELP"}, "keys": ["response"], "timeout": 30},

    # --- Reset (negative) ---
    {"name": "Reset HW (deny)", "method": "POST", "path": "/api/reset/hardware", "body": {"confirm": "WRONG"}, "status": 400},

    # --- Service mode manual ---
    {"name": "Service Enter",   "method": "POST", "path": "/api/service/enter", "keys": ["status", "state"], "timeout": 15},
    {"name": "Service State",   "path": "/api/service/state",      "keys": ["state"]},
    {"name": "Service Exit",    "method": "POST", "path": "/api/service/exit",  "keys": ["status", "state"], "timeout": 15},

    # --- Firmware ---
    {"name": "FW Latest",       "path": "/api/firmware/latest",    "keys": ["status", "latest_version", "download_url"], "timeout": 15},
    {"name": "FW Files",        "path": "/api/firmware/files",     "keys": ["files"]},

    # --- Static ---
    {"name": "HTML",            "path": "/"},
    {"name": "CSS",             "path": "/static/css/style.css"},
    {"name": "JS core",         "path": "/static/js/core.js"},
    {"name": "JS gobius",       "path": "/static/js/gobius.js"},

    # --- Gobius C BLE ---
    {"name": "Gobius Status",   "path": "/api/gobius/status",
     "keys": ["connected", "address", "device", "status", "user_config", "n2k_config"], "timeout": 30},
    {"name": "Gobius N2K Read", "path": "/api/gobius/status",
     "keys": ["n2k_config"], "timeout": 30},
    {"name": "Gobius N2K Write (enable, Water)",
     "method": "POST", "path": "/api/gobius/n2k",
     "body": {"enabled": True, "fluid_instance": 0, "fluid_type": 1, "capacity": 150},
     "keys": ["status", "config"], "timeout": 30},
    {"name": "Gobius User Config Write",
     "method": "POST", "path": "/api/gobius/user_config",
     "body": {"fluid_type": 1, "capacity": 44, "depth": 50},
     "keys": ["status", "config"], "timeout": 30},
    {"name": "Gobius Info Write",
     "method": "POST", "path": "/api/gobius/info",
     "body": {"info1": "Fresh Water", "info2": "Main Tank"},
     "keys": ["status"], "timeout": 20},
    {"name": "BLE Cooldown", "path": "/api/info", "keys": ["state"], "timeout": 10, "_delay": 3},
    {"name": "Gobius Verify After Write", "path": "/api/gobius/status",
     "keys": ["connected", "n2k_config"], "timeout": 30},

    # --- Cleanup ---
    {"name": "Restore AUTO",    "method": "POST", "path": "/api/mode/auto", "keys": ["status"]},
]


# === Run all ===

if __name__ == "__main__":
    print(f"{'='*60}")
    print(f"YDNU-02 API Tests — {BASE}")
    print(f"{'='*60}\n")

    passed, failed, results = 0, 0, []

    for i, spec in enumerate(TESTS, 1):
        name = spec.get("name", spec["path"])
        print(f"--- [{i}] {name} ---")
        ok, label, detail = run_test(spec)
        tag = "✅ PASS" if ok else "❌ FAIL"
        print(f"{tag} {label}  {detail}\n")
        results.append((tag, name))
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed, {passed+failed} total")
    print(f"{'='*60}")
    for tag, name in results:
        print(f"  {tag} {name}")

    sys.exit(1 if failed > 0 else 0)
