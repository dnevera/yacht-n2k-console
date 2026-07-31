import os
import asyncio
import serial
import time
try:
    import pytest
except ImportError:
    pytest = None

try:
    from bleak import BleakClient, BleakScanner
    has_bleak = True
except ImportError:
    has_bleak = False

has_hw = os.path.exists("/dev/ttyACM0")
if pytest is not None:
    pytestmark = pytest.mark.skipif(not (has_bleak and has_hw), reason="Requires bleak library and /dev/ttyACM0 hardware")

GOBIUS_MAC = "2C:A7:74:21:56:D8"
UUID_N2K_CONFIG = "0000fff2-0000-1000-8000-00805f9b34fb"
UUID_N2K_STATUS = "0000fff3-0000-1000-8000-00805f9b34fb"

FLUID_TYPES = {0: "Fuel", 1: "Fresh Water", 2: "Waste Water", 3: "Live Well", 4: "Oil", 5: "Black Water"}
SEP = "=" * 60

def decode_fff2(data):
    print(f"  Raw hex: {data.hex(' ')}")
    n2k_enable = data[0]
    fluid_instance = data[1]
    fluid_type = data[2]
    volume = data[9] if len(data) > 9 else None
    print(f"  N2K Enable:     {n2k_enable} ({'ON' if n2k_enable else 'OFF'})")
    print(f"  Fluid Instance: {fluid_instance}")
    print(f"  Fluid Type:     {fluid_type} ({FLUID_TYPES.get(fluid_type, '?')})")
    print(f"  Volume (byte9): {volume} L")
    print(f"  Full byte map:")
    for i, b in enumerate(data):
        label = ""
        if i == 0: label = "<-- N2K enable"
        elif i == 1: label = "<-- Fluid Instance"
        elif i == 2: label = "<-- Fluid Type"
        elif i == 9: label = "<-- Volume (L)"
        print(f"    [{i:2d}] 0x{b:02X} ({b:3d}) {label}")
    return {"enable": n2k_enable, "instance": fluid_instance, "type": fluid_type, "volume": volume}


async def ble_phase():
    print(f"Connecting BLE to Gobius C ({GOBIUS_MAC})...")
    print("  Scanning for device...")
    device = await BleakScanner.find_device_by_address(GOBIUS_MAC, timeout=10.0)
    if not device:
        print(f"  ERROR: Gobius C not found via BLE scan!")
        return None, None
    print(f"  Found: {device.name} ({device.address})")
    async with BleakClient(device, timeout=15) as client:
        print(f"Connected: {client.is_connected}\n")

        # Step 1: Read current config
        print(SEP)
        print("STEP 1: Read current 0xFFF2 N2K Config")
        print(SEP)
        data = await client.read_gatt_char(UUID_N2K_CONFIG)
        current = decode_fff2(data)

        # Read N2K Status
        print(f"\n0xFFF3 N2K Status:")
        status = await client.read_gatt_char(UUID_N2K_STATUS)
        print(f"  Raw: {status.hex(' ')}")
        print(f"  N2K State: {status[0]} ({'Active' if status[0] == 2 else status[0]})")
        if len(status) > 1:
            print(f"  Source Addr: {status[1]}")

        # Step 2: Write fluid_type = 1 (Water)
        print(f"\n{SEP}")
        print("STEP 2: Write fluid_type=1 (Fresh Water) via BLE")
        print(SEP)
        new_data = bytearray(data)
        new_data[2] = 1  # Fresh Water
        print(f"  Writing: {new_data.hex(' ')}")
        await client.write_gatt_char(UUID_N2K_CONFIG, bytes(new_data), response=True)
        print("  Write OK")

        # Step 3: Read back to verify BLE write
        print(f"\n{SEP}")
        print("STEP 3: Read back 0xFFF2 after write")
        print(SEP)
        data2 = await client.read_gatt_char(UUID_N2K_CONFIG)
        updated = decode_fff2(data2)

        if updated["type"] == 1:
            print("\n  BLE write CONFIRMED: fluid_type is now 1 (Fresh Water)")
        else:
            print(f"\n  BLE write FAILED: fluid_type still {updated['type']}")

    return current, updated


def nmea_phase():
    """Monitor PGN 127505 from Gobius to see if fluid_type changed"""
    print(f"\n{SEP}")
    print("STEP 4: Monitor NMEA PGN 127505 — did fluid_type change?")
    print(SEP)

    port = "/dev/ttyACM0"
    ser = serial.Serial(port, timeout=0.5)
    time.sleep(0.3)
    while ser.in_waiting:
        ser.read(ser.in_waiting)
        time.sleep(0.1)

    print("  Listening for PGN 127505 from SRC 92 (10 seconds)...")
    t0 = time.time()
    count = 0
    while time.time() - t0 < 10.0 and count < 5:
        line = ser.readline().decode("ascii", errors="ignore").strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4 or parts[1] != "R":
            continue
        can_id = int(parts[2], 16)
        src = can_id & 0xFF
        pf = (can_id >> 16) & 0xFF
        if pf >= 240:
            pgn = (pf << 8) | ((can_id >> 8) & 0xFF)
        else:
            pgn = pf << 8

        if src == 92 and pgn == 127505:
            db = [int(x, 16) for x in parts[3:]]
            instance = db[0] & 0x0F
            fluid_type = (db[0] >> 4) & 0x0F
            raw_level = db[1] | (db[2] << 8)
            level_pct = round(raw_level * 0.004, 1)
            raw_cap = int.from_bytes(bytes(db[3:7]), "little")
            cap_l = round(raw_cap * 0.1, 1) if raw_cap != 0xFFFFFFFF else None
            ft_name = FLUID_TYPES.get(fluid_type, "?")
            print(f"  [{parts[0]}] Instance={instance} FluidType={fluid_type} ({ft_name}) Level={level_pct}% Cap={cap_l}L")
            count += 1

    ser.close()

    if count == 0:
        print("  No PGN 127505 received!")


async def main():
    current, updated = await ble_phase()
    if current is None:
        print("BLE phase failed — cannot proceed. Is Gobius C powered on?")
        return
    nmea_phase()

    print(f"\n{SEP}")
    print("SUMMARY")
    print(SEP)
    print(f"  BLE 0xFFF2 fluid_type BEFORE: {current['type']} ({FLUID_TYPES.get(current['type'], '?')})")
    print(f"  BLE 0xFFF2 fluid_type AFTER:  {updated['type']} ({FLUID_TYPES.get(updated['type'], '?')})")
    print(f"  Check NMEA PGN 127505 above: did byte[0] upper nibble change from 0 to 1?")

if __name__ == "__main__":
    asyncio.run(main())
