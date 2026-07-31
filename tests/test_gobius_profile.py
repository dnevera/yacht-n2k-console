import os
import serial
import time
from nmea2000 import NMEA2000Decoder, pgns
try:
    import pytest
except ImportError:
    pytest = None

if pytest is not None:
    pytestmark = pytest.mark.skipif(not os.path.exists("/dev/ttyACM0"), reason="Hardware device /dev/ttyACM0 not present")


def main():
    SEP = "=" * 70
    SEP2 = "-" * 50
    decoder = NMEA2000Decoder()
    port = "/dev/ttyACM0"
    ser = serial.Serial(port, timeout=0.5)
    time.sleep(0.3)
    while ser.in_waiting:
        ser.read(ser.in_waiting)
        time.sleep(0.1)

    ser.write(b"18EAFF10 00 EE 00\r\n")
    time.sleep(0.5)
    ser.write(b"18EAFF10 14 F0 01\r\n")

    print("Collecting all Gobius (SRC 92) traffic for 8 seconds...")
    raw_lines = []
    t0 = time.time()
    while time.time() - t0 < 8.0:
        line = ser.readline().decode("ascii", errors="ignore").strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4 or parts[1] != "R":
            continue
        can_id = int(parts[2], 16)
        src = can_id & 0xFF
        if src == 92:
            raw_lines.append(line)

    ser.close()
    print(f"Captured {len(raw_lines)} frames from SRC 92\n")

    seen_pgns = {}
    for line in raw_lines:
        parts = line.split()
        can_hex = parts[2]
        data_hex = " ".join(parts[3:])
        raw_for_decode = f"{can_hex} {data_hex}"
        try:
            msg = decoder.decode(raw_for_decode)
            if msg:
                pgn = msg.pgn
                desc = msg.description
                if pgn not in seen_pgns:
                    seen_pgns[pgn] = {"description": desc, "count": 0, "sample": msg}
                seen_pgns[pgn]["count"] += 1

                print(f"PGN {pgn:>6} [{desc}]:")
                for f in msg.fields:
                    if f.id.startswith("reserved") or f.id.startswith("spare"):
                        continue
                    iso = getattr(msg, "source_iso_name", None)
                    if iso:
                        seen_pgns[pgn]["iso_name"] = {
                            "unique_number": getattr(iso, "unique_number", None),
                            "manufacturer_code": getattr(iso, "manufacturer_code", None),
                            "device_instance": getattr(iso, "device_instance", None),
                            "device_function": getattr(iso, "device_function", None),
                            "device_class": getattr(iso, "device_class", None),
                            "system_instance": getattr(iso, "system_instance", None),
                            "industry_group": getattr(iso, "industry_group", None),
                        }
        except Exception:
            pass

    print(SEP)
    print("GOBIUS C (SRC 92) - FULL DEVICE PROFILE")
    print(SEP)

    for pgn in sorted(seen_pgns.keys()):
        info = seen_pgns[pgn]
        print(f"\nPGN {pgn} - {info['description']} (seen {info['count']}x)")
        print(SEP2)

    print("\nDone.")


if __name__ == "__main__":
    main()
