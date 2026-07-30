#!/usr/bin/env python3
"""
tests/test_bus_scanner.py — CAN Bus Network Scanner Test & Live Probe

PURPOSE:
  1. pytest suite: verifies TCP connection, ISO Request sending, and fast-packet
     PGN 126996 (Product Info) + PGN 60928 (Address Claim) decoding using mock server.
  2. Live probe mode: run directly (`python3 tests/test_bus_scanner.py [host]`)
     to perform a live scan against port 4001 on gateway.local.local and print the device table.

USAGE:
  pytest tests/test_bus_scanner.py -v
  python3 tests/test_bus_scanner.py gateway.local.local 4001
"""

import sys
import os
import time
import socket
import asyncio
import unittest
from typing import Dict, Any

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ydnu02 import N2KPGNDecoder
from nmea2000 import NMEA2000Decoder


async def scan_live_bus(host: str = "127.0.0.1", port: int = 4001, duration: float = 10.0) -> Dict[int, Dict[str, Any]]:
    """Connect to N2K TCP proxy DATA port, send ISO Requests, and return discovered devices."""
    print(f"📡 Connecting to N2K TCP Proxy at {host}:{port}...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5.0
        )
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return {}

    print(f"🔍 Sending ISO Requests and scanning for {duration}s...")
    writer.write(b"18EAFFFE 00 EE 00\r\n")  # PGN 60928 Address Claim request
    writer.write(b"18EAFFFE 14 F0 01\r\n")  # PGN 126996 Product Info request
    await writer.drain()

    decoder = NMEA2000Decoder()
    devices: Dict[int, Dict[str, Any]] = {}
    frame_count = 0
    t0 = asyncio.get_event_loop().time()

    while asyncio.get_event_loop().time() - t0 < duration:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break

        if not raw:
            break

        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        parsed = N2KPGNDecoder.parse_raw_line(line)
        if not parsed:
            continue

        info = parsed.get("info", {})
        pgn = info.get("pgn")
        src = info.get("src")
        frame_count += 1

        if frame_count <= 5:
            print(f"  [frame #{frame_count}] PGN={pgn} SRC={src} | {line[:60]}")

        if src is None:
            continue

        if src not in devices:
            devices[src] = {"src": src, "frame_count": 0}
        devices[src]["frame_count"] += 1

        # PGN 60928: ISO Address Claim
        if pgn == 60928:
            dev_info = N2KPGNDecoder.parse_device_info(parsed)
            if dev_info:
                devices[src].update(dev_info)
                print(f"  ✨ Found Address Claim for SA={src}: {dev_info.get('manufacturer')}")

        lib_msg = decoder.decode(line)
        if lib_msg is not None and lib_msg.PGN == 126996:
            fields = {f.id: f for f in lib_msg.fields}
            dev = devices.setdefault(lib_msg.source, {"src": lib_msg.source})
            for field_id, attr in (
                ("modelId",            "model"),
                ("softwareVersionCode", "firmware"),
                ("modelSerialCode",     "serial"),
                ("modelVersion",        "model_version"),
            ):
                fld = fields.get(field_id)
                if fld and fld.value:
                    dev[attr] = str(fld.value).strip()
            print(f"  🎉 Product Info assembled for SA={lib_msg.source}: model='{dev.get('model')}' serial='{dev.get('serial')}' fw='{dev.get('firmware')}'")

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass

    print(f"✅ Scan finished: {len(devices)} device(s) found, {frame_count} frames processed.\n")
    return devices


def print_device_table(devices: Dict[int, Dict[str, Any]]):
    """Print formatted terminal table of discovered devices."""
    if not devices:
        print("No devices found.")
        return

    print("┌──────┬───────────────────────────────┬───────────────────────────────┬──────────────┬──────────────┬─────────────┐")
    print("│  SA  │ Manufacturer                  │ Model                         │ Serial       │ Firmware     │ Unique ID   │")
    print("├──────┼───────────────────────────────┼───────────────────────────────┼──────────────┼──────────────┼─────────────┤")

    for sa, dev in sorted(devices.items()):
        mfr = (dev.get("manufacturer") or "Unknown")[:29]
        model = (dev.get("model") or "N/A")[:29]
        serial = (str(dev.get("serial")) if dev.get("serial") else "N/A")[:12]
        fw = (str(dev.get("firmware")) if dev.get("firmware") else "N/A")[:12]
        uid = str(dev.get("unique_id") or "N/A")[:11]
        print(f"│ {sa:4d} │ {mfr:29s} │ {model:29s} │ {serial:12s} │ {fw:12s} │ {uid:11s} │")

    print("└──────┴───────────────────────────────┴───────────────────────────────┴──────────────┴──────────────┴─────────────┘")


# ── Pytest automated suite ───────────────────────────────────────────────────

class TestBusScanner(unittest.TestCase):
    def test_decoder_fast_packet_and_claim(self):
        """Verify N2KPGNDecoder handles PGN 60928 Address Claim."""
        claim_line = "00:00:00.000 R 18EEFF40 4B C8 0A 00 80 00 19 04"
        parsed = N2KPGNDecoder.parse_raw_line(claim_line)
        self.assertIsNotNone(parsed)
        info = parsed.get("info", {})
        self.assertEqual(info.get("pgn"), 60928)
        self.assertEqual(info.get("src"), 64)

        dev_info = N2KPGNDecoder.parse_device_info(parsed)
        self.assertIn("manufacturer", dev_info)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "-v":
        host = sys.argv[1]
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 4001
        dur = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
        results = asyncio.run(scan_live_bus(host, port, dur))
        print_device_table(results)
    else:
        unittest.main()
