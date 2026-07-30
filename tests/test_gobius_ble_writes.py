"""
tests/test_gobius_ble_writes.py — BLE Write Logic Tests for Gobius C.

Tests the byte-level encoding logic used in routes/gobius.py before
writing to GATT characteristics. No real BLE connection needed.

Protocol reference: "GOBIUS C Bluetooth Protocol & Functional Description"
  Issue 3, 2023-08-08, Gobius Sensor Technology AB.
  All multi-byte values are Big-Endian (BE).

Characteristics under test:
  0xFFE6  User Config     R/W  20 bytes — geometry (dist empty/full), LP filters
  0xFFF2  N2K Config      R/W  20 bytes — enabled, fluid_type, instance, volume_l
  0xFFE7  Command         W     3 bytes — calibrate, initialize, stop, start, etc.
  0xFFEB  Info 1          R/W  20 bytes — ASCII label (padded to 20)
  0xFFEC  Info 2          R/W  20 bytes — ASCII label (padded to 20)
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gobius_parsers import parse_user_cfg, parse_n2k_cfg, FLUID_TYPES

# ─── Real sensor dumps used as starting point for read-modify-write ───────────
# These are actual GATT reads captured from a live Gobius C sensor.
REAL_FFE6 = bytes.fromhex("012c003203151032053205000000000000000a00")
REAL_FFF2 = bytes.fromhex("0100010000000000009600000000000000000000")

# Commands (0xFFE7) — 3-byte encoding from spec Table 18
COMMANDS = {
    "initialize": b"i",
    "calibrate":  b"c",
    "stop":       b"a",
    "start":      b"b",
    "adv_normal": b"n",
    "adv_off":    b"o",
    "write_info": b"w",
    "secure":     b"s",
    "unsecure":   b"u",
}


# ─── Helper: simulate routes/gobius.py write logic ────────────────────────────

def apply_user_cfg_write(base: bytes, body: dict) -> bytes:
    """
    Simulate gobius_write_user_config() byte patching logic.
    Mirrors routes/gobius.py::gobius_write_user_config exactly.
    """
    old = bytearray(base)
    if "distance_empty_mm" in body:
        val = max(20, min(2000, int(body["distance_empty_mm"])))
        old[0:2] = val.to_bytes(2, "big")
    if "distance_full_mm" in body:
        val = max(20, min(2000, int(body["distance_full_mm"])))
        old[2:4] = val.to_bytes(2, "big")
    if "lp_filter_n" in body:
        old[4] = max(0, min(100, int(body["lp_filter_n"])))
    if "lp_filter_k" in body:
        old[5] = max(1, min(100, int(body["lp_filter_k"])))
    if "config_bits" in body:
        old[6] = int(body["config_bits"]) & 0xFF
    if "out1_threshold" in body:
        old[7] = max(0, min(100, int(body["out1_threshold"])))
    if "out1_hysteresis" in body:
        old[8] = max(0, min(100, int(body["out1_hysteresis"])))
    if "out2_threshold" in body:
        old[9] = max(0, min(100, int(body["out2_threshold"])))
    if "out2_hysteresis" in body:
        old[10] = max(0, min(100, int(body["out2_hysteresis"])))
    if "advertise_off_s" in body:
        old[18] = max(10, min(255, int(body["advertise_off_s"])))
    return bytes(old)


def apply_n2k_cfg_write(base: bytes, body: dict) -> bytes:
    """
    Simulate gobius_write_n2k() byte patching logic.
    Mirrors routes/gobius.py::gobius_write_n2k exactly.
    """
    old = bytearray(base)
    if "enabled" in body:
        old[0] = 0x01 if body["enabled"] else 0x00
    if "fluid_instance" in body:
        old[1] = int(body["fluid_instance"]) & 0x0F
    if "fluid_type" in body:
        old[2] = int(body["fluid_type"]) & 0xFF
    if "volume_l" in body:
        vol = max(1, min(255, int(body["volume_l"])))
        old[9] = vol
    return bytes(old)


def encode_command(cmd_name: str, param: int = 0) -> bytes:
    """
    Simulate gobius_send_command() encoding.
    3-byte: [cmd_code, param_high, param_low]
    """
    cmd_bytes = bytearray(3)
    cmd_bytes[0] = COMMANDS[cmd_name][0]
    cmd_bytes[1:3] = param.to_bytes(2, "big")
    return bytes(cmd_bytes)


def encode_info(value: str) -> bytes:
    """Simulate gobius_write_info(): pad/truncate to 20 bytes UTF-8."""
    return str(value)[:20].ljust(20).encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 0xFFE6 User Config write tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserCfgWrite:
    """0xFFE6 — geometry and filter writes."""

    def test_distance_empty_big_endian(self):
        """UC_DE bytes[0:2] must be Big-Endian uint16."""
        result = apply_user_cfg_write(REAL_FFE6, {"distance_empty_mm": 400})
        assert result[0] == 0x01  # 400 = 0x0190
        assert result[1] == 0x90
        parsed = parse_user_cfg(result)
        assert parsed["distance_empty_mm"] == 400

    def test_distance_full_big_endian(self):
        """UC_DF bytes[2:4] must be Big-Endian uint16."""
        result = apply_user_cfg_write(REAL_FFE6, {"distance_full_mm": 80})
        assert result[2] == 0x00  # 80 = 0x0050
        assert result[3] == 0x50
        parsed = parse_user_cfg(result)
        assert parsed["distance_full_mm"] == 80

    def test_lp_filter_n_byte4(self):
        """UC_LPN at byte[4]."""
        result = apply_user_cfg_write(REAL_FFE6, {"lp_filter_n": 5})
        assert result[4] == 5
        parsed = parse_user_cfg(result)
        assert parsed["lp_filter_n"] == 5

    def test_lp_filter_k_byte5(self):
        """UC_LPK at byte[5]."""
        result = apply_user_cfg_write(REAL_FFE6, {"lp_filter_k": 20})
        assert result[5] == 20
        parsed = parse_user_cfg(result)
        assert parsed["lp_filter_k"] == 20

    def test_other_bytes_unchanged(self):
        """Write only distance_empty — all other bytes must stay unchanged."""
        original = parse_user_cfg(REAL_FFE6)
        result = apply_user_cfg_write(REAL_FFE6, {"distance_empty_mm": 350})
        parsed = parse_user_cfg(result)
        assert parsed["distance_full_mm"] == original["distance_full_mm"]
        assert parsed["lp_filter_n"] == original["lp_filter_n"]
        assert parsed["lp_filter_k"] == original["lp_filter_k"]
        assert parsed["config_bits"] == original["config_bits"]

    def test_roundtrip_geometry(self):
        """Write → parse → values match exactly."""
        body = {"distance_empty_mm": 500, "distance_full_mm": 30,
                "lp_filter_n": 7, "lp_filter_k": 15}
        result = apply_user_cfg_write(REAL_FFE6, body)
        parsed = parse_user_cfg(result)
        assert parsed["distance_empty_mm"] == 500
        assert parsed["distance_full_mm"] == 30
        assert parsed["lp_filter_n"] == 7
        assert parsed["lp_filter_k"] == 15

    def test_distance_clamp_min(self):
        """Values below 20mm must be clamped to 20mm (spec min)."""
        result = apply_user_cfg_write(REAL_FFE6, {"distance_empty_mm": 5})
        parsed = parse_user_cfg(result)
        assert parsed["distance_empty_mm"] == 20

    def test_distance_clamp_max(self):
        """Values above 2000mm must be clamped to 2000mm (spec max)."""
        result = apply_user_cfg_write(REAL_FFE6, {"distance_empty_mm": 9999})
        parsed = parse_user_cfg(result)
        assert parsed["distance_empty_mm"] == 2000

    def test_lp_filter_n_clamp(self):
        """lp_filter_n must be clamped to [0, 100]."""
        result = apply_user_cfg_write(REAL_FFE6, {"lp_filter_n": 200})
        assert result[4] == 100
        result = apply_user_cfg_write(REAL_FFE6, {"lp_filter_n": -5})
        assert result[4] == 0

    def test_lp_filter_k_clamp_min(self):
        """lp_filter_k minimum is 1 (0 would disable, spec says 1-100)."""
        result = apply_user_cfg_write(REAL_FFE6, {"lp_filter_k": 0})
        assert result[5] == 1

    def test_lp_filter_k_clamp_max(self):
        """lp_filter_k maximum is 100."""
        result = apply_user_cfg_write(REAL_FFE6, {"lp_filter_k": 255})
        assert result[5] == 100

    def test_output_threshold_byte7(self):
        """UC_O1T at byte[7] — Output 1 threshold %."""
        result = apply_user_cfg_write(REAL_FFE6, {"out1_threshold": 75})
        assert result[7] == 75

    def test_output_hysteresis_byte8(self):
        """UC_O1H at byte[8] — Output 1 hysteresis %."""
        result = apply_user_cfg_write(REAL_FFE6, {"out1_hysteresis": 10})
        assert result[8] == 10

    def test_advertise_off_byte18(self):
        """UC_AOF at byte[18] — Advertise-off time [10-255 s]."""
        result = apply_user_cfg_write(REAL_FFE6, {"advertise_off_s": 60})
        assert result[18] == 60

    def test_advertise_off_clamp_min(self):
        """Advertise-off minimum is 10s per spec."""
        result = apply_user_cfg_write(REAL_FFE6, {"advertise_off_s": 3})
        assert result[18] == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 0xFFF2 N2K Config write tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestN2kCfgWrite:
    """0xFFF2 — N2K enable, fluid type, instance, volume writes."""

    def test_enable_byte0(self):
        """byte[0] = 0x01 when enabled=True."""
        result = apply_n2k_cfg_write(REAL_FFF2, {"enabled": True})
        assert result[0] == 0x01
        parsed = parse_n2k_cfg(result)
        assert parsed["n2k_enabled"] is True

    def test_disable_byte0(self):
        """byte[0] = 0x00 when enabled=False."""
        result = apply_n2k_cfg_write(REAL_FFF2, {"enabled": False})
        assert result[0] == 0x00
        parsed = parse_n2k_cfg(result)
        assert parsed["n2k_enabled"] is False

    def test_fluid_instance_byte1(self):
        """byte[1] = fluid_instance, masked to lower nibble (0x0F)."""
        result = apply_n2k_cfg_write(REAL_FFF2, {"fluid_instance": 2})
        assert result[1] == 2
        parsed = parse_n2k_cfg(result)
        assert parsed["fluid_instance"] == 2

    def test_fluid_instance_nibble_mask(self):
        """fluid_instance is masked to 4 bits (max 15)."""
        result = apply_n2k_cfg_write(REAL_FFF2, {"fluid_instance": 0xFF})
        assert result[1] == 0x0F  # 0xFF & 0x0F = 0x0F

    def test_fluid_type_byte2(self):
        """byte[2] = fluid_type code."""
        for code, name in FLUID_TYPES.items():
            result = apply_n2k_cfg_write(REAL_FFF2, {"fluid_type": code})
            assert result[2] == code
            parsed = parse_n2k_cfg(result)
            assert parsed["fluid_type"] == code
            assert parsed["fluid_type_name"] == name

    def test_volume_byte9(self):
        """volume_l stored at byte[9] as uint8 (max 255L)."""
        result = apply_n2k_cfg_write(REAL_FFF2, {"volume_l": 50})
        assert result[9] == 50
        parsed = parse_n2k_cfg(result)
        assert parsed["volume_l"] == 50

    def test_volume_clamp_max_255(self):
        """volume_l is a single byte — max 255L."""
        result = apply_n2k_cfg_write(REAL_FFF2, {"volume_l": 300})
        assert result[9] == 255

    def test_volume_clamp_min_1(self):
        """volume_l minimum is 1L (0 is invalid per spec)."""
        result = apply_n2k_cfg_write(REAL_FFF2, {"volume_l": 0})
        assert result[9] == 1

    def test_other_bytes_unchanged(self):
        """Write only enabled flag — fluid_type and volume unchanged."""
        original = parse_n2k_cfg(REAL_FFF2)
        result = apply_n2k_cfg_write(REAL_FFF2, {"enabled": False})
        parsed = parse_n2k_cfg(result)
        assert parsed["fluid_type"] == original["fluid_type"]
        assert parsed["volume_l"] == original["volume_l"]
        assert parsed["fluid_instance"] == original["fluid_instance"]

    def test_roundtrip_full_config(self):
        """Write all fields → parse → values match exactly."""
        body = {
            "enabled": True,
            "fluid_instance": 1,
            "fluid_type": 2,  # Gray Water
            "volume_l": 120,
        }
        result = apply_n2k_cfg_write(REAL_FFF2, body)
        parsed = parse_n2k_cfg(result)
        assert parsed["n2k_enabled"] is True
        assert parsed["fluid_instance"] == 1
        assert parsed["fluid_type"] == 2
        assert parsed["fluid_type_name"] == "Gray Water"
        assert parsed["volume_l"] == 120

    def test_toggling_does_not_corrupt_data(self):
        """Disable then re-enable — fluid_type and volume must survive."""
        start = apply_n2k_cfg_write(REAL_FFF2, {
            "enabled": True, "fluid_type": 4, "volume_l": 80
        })
        disabled = apply_n2k_cfg_write(start, {"enabled": False})
        re_enabled = apply_n2k_cfg_write(disabled, {"enabled": True})
        parsed = parse_n2k_cfg(re_enabled)
        assert parsed["n2k_enabled"] is True
        assert parsed["fluid_type"] == 4    # Oil — must survive toggle
        assert parsed["volume_l"] == 80     # must survive toggle


# ═══════════════════════════════════════════════════════════════════════════════
# 0xFFE7 Command encoding tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCommandEncoding:
    """0xFFE7 — 3-byte command frame encoding."""

    def test_all_commands_are_3_bytes(self):
        """Every command must produce exactly 3 bytes."""
        for cmd in COMMANDS:
            encoded = encode_command(cmd)
            assert len(encoded) == 3, f"{cmd}: expected 3 bytes, got {len(encoded)}"

    def test_initialize_code(self):
        """initialize → byte[0] = ord('i') = 0x69."""
        encoded = encode_command("initialize")
        assert encoded[0] == ord("i")
        assert encoded[1] == 0x00
        assert encoded[2] == 0x00

    def test_calibrate_code(self):
        """calibrate → byte[0] = ord('c') = 0x63."""
        encoded = encode_command("calibrate")
        assert encoded[0] == ord("c")

    def test_stop_code(self):
        """stop → byte[0] = ord('a')."""
        encoded = encode_command("stop")
        assert encoded[0] == ord("a")

    def test_start_code(self):
        """start → byte[0] = ord('b')."""
        encoded = encode_command("start")
        assert encoded[0] == ord("b")

    def test_write_info_code(self):
        """write_info → byte[0] = ord('w') — must follow info1/info2 write."""
        encoded = encode_command("write_info")
        assert encoded[0] == ord("w")

    def test_param_big_endian_bytes_1_2(self):
        """param is encoded as BE uint16 in bytes[1:3]."""
        encoded = encode_command("calibrate", param=0x0102)
        assert encoded[1] == 0x01
        assert encoded[2] == 0x02

    def test_param_zero_by_default(self):
        """No param → bytes[1:3] = 0x0000."""
        encoded = encode_command("start")
        assert encoded[1] == 0x00
        assert encoded[2] == 0x00


# ═══════════════════════════════════════════════════════════════════════════════
# 0xFFEB / 0xFFEC Info fields encoding tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestInfoEncoding:
    """0xFFEB/0xFFEC — 20-byte ASCII label encoding."""

    def test_short_string_padded_to_20(self):
        """Short strings must be right-padded with spaces to 20 bytes."""
        encoded = encode_info("Tank A")
        assert len(encoded) == 20
        assert encoded[:6] == b"Tank A"
        assert encoded[6:] == b" " * 14

    def test_exact_20_chars_no_truncation(self):
        """Exactly 20 chars must fit without truncation."""
        s = "A" * 20
        encoded = encode_info(s)
        assert len(encoded) == 20
        assert encoded == b"A" * 20

    def test_string_truncated_at_20(self):
        """Strings longer than 20 chars must be truncated to 20."""
        long_str = "Fresh Water Main Tank"  # 21 chars
        encoded = encode_info(long_str)
        assert len(encoded) == 20
        assert encoded == b"Fresh Water Main Tank"[:20]

    def test_empty_string_is_20_spaces(self):
        """Empty string produces 20 space bytes."""
        encoded = encode_info("")
        assert len(encoded) == 20
        assert encoded == b" " * 20

    def test_utf8_encoding(self):
        """Info fields are UTF-8 encoded per write_char call."""
        encoded = encode_info("Fuel")
        assert encoded == b"Fuel" + b" " * 16

    def test_roundtrip_read_back(self):
        """Simulate sensor read: bytes.decode().strip() == original value."""
        original = "Fresh Water"
        encoded = encode_info(original)
        read_back = encoded.decode("utf-8", errors="replace").strip()
        assert read_back == original


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-characteristic consistency tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtocolConsistency:
    """Cross-field checks to verify protocol invariants."""

    def test_real_dump_ffe6_parse_roundtrip(self):
        """Real REAL_FFE6 → parse → re-encode same values → bytes identical."""
        parsed = parse_user_cfg(REAL_FFE6)
        result = apply_user_cfg_write(REAL_FFE6, {
            "distance_empty_mm": parsed["distance_empty_mm"],
            "distance_full_mm": parsed["distance_full_mm"],
            "lp_filter_n": parsed["lp_filter_n"],
            "lp_filter_k": parsed["lp_filter_k"],
        })
        reparsed = parse_user_cfg(result)
        assert reparsed["distance_empty_mm"] == parsed["distance_empty_mm"]
        assert reparsed["distance_full_mm"] == parsed["distance_full_mm"]
        assert reparsed["lp_filter_n"] == parsed["lp_filter_n"]
        assert reparsed["lp_filter_k"] == parsed["lp_filter_k"]

    def test_real_dump_fff2_parse_roundtrip(self):
        """Real REAL_FFF2 → parse → re-encode same values → values identical."""
        parsed = parse_n2k_cfg(REAL_FFF2)
        result = apply_n2k_cfg_write(REAL_FFF2, {
            "enabled": parsed["n2k_enabled"],
            "fluid_instance": parsed["fluid_instance"],
            "fluid_type": parsed["fluid_type"],
            "volume_l": parsed["volume_l"],
        })
        reparsed = parse_n2k_cfg(result)
        assert reparsed["n2k_enabled"] == parsed["n2k_enabled"]
        assert reparsed["fluid_instance"] == parsed["fluid_instance"]
        assert reparsed["fluid_type"] == parsed["fluid_type"]
        assert reparsed["volume_l"] == parsed["volume_l"]

    def test_write_info_must_follow_info_write(self):
        """
        Spec requires: write info1/info2 → THEN send command write_info ('w').
        Verify both produce consistent byte patterns.
        """
        info_bytes = encode_info("Main Tank")
        cmd_bytes = encode_command("write_info")
        assert len(info_bytes) == 20
        assert cmd_bytes[0] == ord("w")
        assert cmd_bytes[1:] == b"\x00\x00"  # no param needed

    def test_volume_single_byte_max(self):
        """
        volume_l is stored in a single byte (byte[9] of 0xFFF2).
        Max representable value = 255L. Values above must be clamped.
        This is a fundamental protocol limitation.
        """
        result = apply_n2k_cfg_write(REAL_FFF2, {"volume_l": 1000})
        assert result[9] == 255, "volume_l overflow must be clamped to 255"

    def test_fluid_type_byte_mask(self):
        """
        fluid_type is a full byte (0xFF mask) — all 8 bits used.
        fluid_instance is nibble-masked (0x0F) — only lower 4 bits.
        """
        result = apply_n2k_cfg_write(REAL_FFF2, {
            "fluid_type": 0xFF,     # full byte, all bits
            "fluid_instance": 0xFF, # nibble mask → 0x0F
        })
        assert result[2] == 0xFF
        assert result[1] == 0x0F
