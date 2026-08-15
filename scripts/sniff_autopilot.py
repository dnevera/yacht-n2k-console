#!/usr/bin/env python3
"""
sniff_autopilot.py — READ-ONLY sniffer for Raymarine autopilot frames.

Connects to the gateway DATA port (:4001), keeps only the autopilot-related
PGNs, appends the raw lines to a file and prints the decoded interpretation
next to them. It NEVER writes a single byte to the socket — the point is to
confirm the reverse-engineered byte layouts in n2k_autopilot.py against what
the p70 displays, before any command is ever sent to a device that steers the
boat. See specs/active/008-autopilot-control.md.

Usage:
    python scripts/sniff_autopilot.py --host 192.168.1.50 --out autopilot.log

How to verify on board:
    1. Start the sniffer and watch the "mode" column.
    2. Put the p70 into Auto — the mode must flip standby -> auto.
    3. Nudge the locked heading by -10 / +10 — locked_heading_deg must follow
       the number on the p70 (magnetic).
    4. Turn the wheel in Standby — rudder_angle_deg must change sign with the
       direction.
    Any mismatch means the layout constants in n2k_autopilot.py are wrong for
    this EV firmware and must be corrected before trusting the API.
"""

import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import n2k_autopilot as ap  # noqa: E402
from ydnu02 import N2KPGNDecoder  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4001


def _describe(decoded):
    return ", ".join(f"{k}={v}" for k, v in decoded.items() if k != "kind")


def sniff(host: str, port: int, out_path: str, duration: float) -> int:
    print(f"[sniff] connecting to {host}:{port} (read only)")
    sock = socket.create_connection((host, port), timeout=10)
    # Belt and braces: never send anything on this socket.
    sock.shutdown(socket.SHUT_WR)
    deadline = time.time() + duration if duration > 0 else None
    seen = 0
    buf = b""

    with open(out_path, "a", encoding="utf-8") as out:
        out.write(f"# sniff_autopilot start {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        try:
            while deadline is None or time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    print("[sniff] connection closed by gateway")
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("ascii", errors="ignore").strip()
                    if not text:
                        continue
                    parsed = N2KPGNDecoder.parse_raw_line(text)
                    if not parsed:
                        continue
                    pgn = parsed.get("info", {}).get("pgn")
                    if pgn not in ap.AUTOPILOT_PGNS:
                        continue
                    src = parsed.get("info", {}).get("src")
                    seen += 1
                    out.write(text + "\n")

                    if pgn == ap.PROPRIETARY_PGN:
                        # Fast-packet: let the library reassemble it, exactly as
                        # the gateway does — never reassemble by hand.
                        lib_msg = N2KPGNDecoder.feed_to_lib(parsed)
                        payload = getattr(lib_msg, "raw_can_data", None) if lib_msg else None
                        if not isinstance(payload, (bytes, bytearray)):
                            continue
                        decoded = ap.decode_126720(bytes(payload))
                    else:
                        decoded = ap.decode_frame(pgn, parsed.get("data", b""))

                    if decoded:
                        print(f"[{time.strftime('%H:%M:%S')}] PGN {pgn} SRC {src} "
                              f"{decoded['kind']}: {_describe(decoded)}")
                        out.flush()
        except KeyboardInterrupt:
            print("\n[sniff] stopped by user")
        finally:
            sock.close()
            out.write(f"# sniff_autopilot stop, {seen} autopilot frames\n")

    print(f"[sniff] {seen} autopilot frames written to {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Raymarine autopilot sniffer")
    parser.add_argument("--host", default=DEFAULT_HOST, help="gateway host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="DATA port (default 4001)")
    parser.add_argument("--out", default="autopilot_sniff.log", help="raw frame dump file")
    parser.add_argument("--duration", type=float, default=0,
                        help="seconds to capture, 0 = until Ctrl-C")
    args = parser.parse_args()
    try:
        return sniff(args.host, args.port, args.out, args.duration)
    except OSError as exc:
        print(f"[sniff] cannot connect to {args.host}:{args.port}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
