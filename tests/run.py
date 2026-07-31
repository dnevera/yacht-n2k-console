#!/usr/bin/env python3
"""
YDNU-02 API test runner.
Run: python3 -m tests.run [base_url]
  or: python3 tests/run.py [base_url]
"""
import sys
import os
import json
import time
import urllib.request
import urllib.error

# Allow running from project root or tests/ dir
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from specs import ALL as TESTS

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://<gateway-host>:8080"


def run_test(spec: dict) -> tuple[bool, str, str]:
    """Execute a single test spec → (pass, label, detail)."""
    method = spec.get("method", "GET")
    path = spec["path"]
    body = spec.get("body")
    expect_keys = spec.get("keys", [])
    expect_status = spec.get("status", 200)
    timeout = spec.get("timeout", 30)
    label = f"{method} {path}"
    url = BASE + path

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


def main():
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


if __name__ == "__main__":
    main()
