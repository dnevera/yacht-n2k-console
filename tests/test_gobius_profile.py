"""Full device profile decoder: collects and decodes all Gobius C traffic"""
import serial, time
from nmea2000 import NMEA2000Decoder, pgns

SEP = "=" * 70
SEP2 = "-" * 50

decoder = NMEA2000Decoder()
port = "/dev/ttyACM0"
ser = serial.Serial(port, timeout=0.5)
time.sleep(0.3)
while ser.in_waiting:
    ser.read(ser.in_waiting)
    time.sleep(0.1)

# Request Address Claim + Product Info from all devices
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

# Decode each frame
seen_pgns = {}
for line in raw_lines:
    parts = line.split()
    can_hex = parts[2]
    data_hex = " ".join(parts[3:])
    raw_for_decode = f"{can_hex} {data_hex}"

    try:
        msg = decoder.decode(raw_for_decode)
        if not msg:
            continue
        pgn = msg.PGN
        if pgn not in seen_pgns:
            seen_pgns[pgn] = {
                "description": msg.description or msg.id,
                "count": 0,
                "first_fields": {},
                "iso_name": None,
            }
        seen_pgns[pgn]["count"] += 1

        if seen_pgns[pgn]["count"] == 1:
            for f in msg.fields:
                fid = f.id
                if fid.startswith("reserved") or fid.startswith("spare"):
                    continue
                seen_pgns[pgn]["first_fields"][fid] = {
                    "name": f.name,
                    "value": f.value,
                    "raw_value": f.raw_value,
                    "type": str(f.type),
                    "unit": f.unit_of_measurement,
                }

            if hasattr(msg, "source_iso_name") and msg.source_iso_name:
                iso = msg.source_iso_name
                seen_pgns[pgn]["iso_name"] = {
                    "unique_number": getattr(iso, "unique_number", None),
                    "manufacturer_code": getattr(iso, "manufacturer_code", None),
                    "device_instance": getattr(iso, "device_instance", None),
                    "device_function": getattr(iso, "device_function", None),
                    "device_class": getattr(iso, "device_class", None),
                    "system_instance": getattr(iso, "system_instance", None),
                    "industry_group": getattr(iso, "industry_group", None),
                }
    except Exception as e:
        pass

# Print results
print(SEP)
print("GOBIUS C (SRC 92) - FULL DEVICE PROFILE")
print(SEP)

for pgn in sorted(seen_pgns.keys()):
    info = seen_pgns[pgn]
    print(f"\nPGN {pgn} - {info['description']} (seen {info['count']}x)")
    print(SEP2)

    if info["iso_name"]:
        print("  ISO Name:")
        for k, v in info["iso_name"].items():
            print(f"    {k}: {v}")

    for fid, fdata in info["first_fields"].items():
        val = fdata["value"]
        raw = fdata["raw_value"]
        unit = fdata["unit"] or ""
        typ = fdata["type"]
        print(f"  {fid}: {val} (raw={raw}) {unit} [{typ}]")

# Lookup device class/function names
print("")
print(SEP)
print("NMEA 2000 DEVICE CLASS/FUNCTION LOOKUP")
print(SEP)

iso_data = seen_pgns.get(60928, {}).get("iso_name")
if iso_data:
    print(f"  manufacturer_code: {iso_data['manufacturer_code']}")
    print(f"  device_function:   {iso_data['device_function']}")
    print(f"  device_class:      {iso_data['device_class']}")
    print(f"  industry_group:    {iso_data['industry_group']}")
    print(f"  unique_number:     {iso_data['unique_number']}")
    print(f"  device_instance:   {iso_data['device_instance']}")

    for key, val in [
        ("MANUFACTURER_CODE", iso_data["manufacturer_code"]),
        ("DEVICE_FUNCTION", iso_data["device_function"]),
        ("DEVICE_CLASS", iso_data["device_class"]),
        ("INDUSTRY_CODE", iso_data["industry_group"]),
    ]:
        if key in pgns.master_dict and val is not None:
            lookup = pgns.master_dict[key]
            resolved = lookup.get(int(val)) if isinstance(val, (int, float)) else lookup.get(val)
            if resolved:
                print(f"  {key} -> {resolved}")

# Also: show ALL PGNs we ever see from SRC 92
print("")
print(SEP)
print("OBSERVED PGN SUMMARY (SRC 92)")
print(SEP)
for pgn in sorted(seen_pgns.keys()):
    info = seen_pgns[pgn]
    print(f"  PGN {pgn:>6}: {info['description']:<40} ({info['count']} frames)")

# Check which PGN 126208 functions the library knows about
print("")
print(SEP)
print("LIBRARY: PGN 126208 SUB-FUNCTIONS AVAILABLE")
print(SEP)
funcs = [f for f in dir(pgns) if "126208" in f and (f.startswith("decode") or f.startswith("encode"))]
for f in sorted(funcs):
    prefix = "DECODE" if f.startswith("decode") else "ENCODE"
    name = f.replace("decode_pgn_126208_", "").replace("encode_pgn_126208_", "")
    print(f"  [{prefix}] {name}")

print("\nDone.")
