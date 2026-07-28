"""Protocol audit: test all PGN 126208 variants against Gobius C (SRC 92)"""
import serial, time

port = "/dev/ttyACM0"
ser = serial.Serial(port, timeout=0.5)
time.sleep(0.3)
while ser.in_waiting:
    ser.read(ser.in_waiting)
    time.sleep(0.1)

SEP = "=" * 60

def parse_can_id(hex_str):
    can_id = int(hex_str, 16)
    priority = (can_id >> 26) & 0x7
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    src = can_id & 0xFF
    if pf < 240:
        pgn = pf << 8
        dst = ps
    else:
        pgn = (pf << 8) | ps
        dst = 255
    return {"priority": priority, "pf": pf, "pgn": pgn, "src": src, "dst": dst}

def send_and_listen(name, frame, listen_sec=3.0, filter_src=None):
    while ser.in_waiting:
        ser.read(ser.in_waiting)
    print(f"\n{SEP}")
    print(f"TEST: {name}")
    frame_clean = frame.strip()
    print(f"TX: {frame_clean}")
    info = parse_can_id(frame_clean.split()[0])
    payload = " ".join(frame_clean.split()[1:])
    print(f"    Priority={info['priority']} PGN={info['pgn']} DST={info['dst']} SRC={info['src']}")
    print(f"    Payload: {payload}")
    ser.write((frame_clean + "\r\n").encode())

    t0 = time.time()
    replies = []
    while time.time() - t0 < listen_sec:
        line = ser.readline().decode("ascii", errors="ignore").strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4 or parts[1] != "R":
            continue
        ri = parse_can_id(parts[2])
        if filter_src and ri["src"] != filter_src:
            continue
        data = " ".join(parts[3:])
        print(f"  RX: PGN {ri['pgn']:>6} SRC={ri['src']} DST={ri['dst']} | {data}")
        
        # Decode ISO ACK
        if ri["pgn"] == 59392:
            db = [int(x, 16) for x in parts[3:] if len(x) == 2]
            ack_codes = {0: "ACK", 1: "NAK", 2: "Access Denied", 3: "Address Busy"}
            code = db[0] if db else -1
            ref_pgn = (db[5] | (db[6] << 8) | (db[7] << 16)) if len(db) >= 8 else 0
            print(f"       -> ISO ACK: {ack_codes.get(code, code)} for PGN {ref_pgn}")
        
        replies.append((ri, data))
    if not replies:
        print(f"  (no response from SRC {filter_src} in {listen_sec}s)")
    return replies

# TEST 1: Address Claim — SHOULD work
send_and_listen("ISO Request -> Address Claim (60928)", "18EAFF10 00 EE 00", 3, 92)

# TEST 2: Product Info — SHOULD work
send_and_listen("ISO Request -> Product Info (126996)", "18EAFF10 14 F0 01", 3, 92)

# TEST 3: PGN List
send_and_listen("ISO Request -> PGN List (126464)", "18EAFF10 C0 EE 01", 3, 92)

# TEST 4: Command FC=1 (correct NMEA standard)
send_and_listen("126208 Command FC=1 (correct)", "18ED5C10 01 11 F2 01 08 02 01 00 02 01", 3, 92)

# TEST 5: Command FC=0 (legacy bug in n2k_command_builder.py)
send_and_listen("126208 Command FC=0 (legacy bug)", "18ED5C10 00 11 F2 01 08 02 01 00 02 01", 3, 92)

# TEST 6: Read Fields FC=3
send_and_listen("126208 Read Fields FC=3", "0CED5C10 03 11 F2 01 FF FF FF 00 FF", 3, 92)

# TEST 7: Write Fields FC=5
send_and_listen("126208 Write Fields FC=5", "0CED5C10 05 11 F2 01 FF FF FF 00 02 01 00 02 01", 3, 92)

# TEST 8: Request Group FC=0 (some devices)
send_and_listen("126208 Request Group FC=0", "0CED5C10 00 11 F2 01 FF FF FF FF", 3, 92)

# Decode regular Gobius broadcast
print(f"\n{SEP}")
print("REFERENCE: Gobius PGN 127505 broadcast decode")
t0 = time.time()
while time.time() - t0 < 5.0:
    line = ser.readline().decode("ascii", errors="ignore").strip()
    if not line:
        continue
    parts = line.split()
    if len(parts) < 4 or parts[1] != "R":
        continue
    ri = parse_can_id(parts[2])
    if ri["src"] == 92 and ri["pgn"] == 127505:
        db = [int(x, 16) for x in parts[3:]]
        instance = db[0] & 0x0F
        fluid_type = (db[0] >> 4) & 0x0F
        raw_level = db[1] | (db[2] << 8)
        level_pct = round(raw_level * 0.004, 1)
        raw_cap = int.from_bytes(bytes(db[3:7]), "little")
        cap_l = round(raw_cap * 0.1, 1) if raw_cap != 0xFFFFFFFF else None
        print(f"  Raw bytes: {' '.join(parts[3:])}")
        print(f"  Instance={instance} FluidType={fluid_type} Level={level_pct}% Capacity={cap_l}L")
        break

ser.close()
print("\nDone.")
