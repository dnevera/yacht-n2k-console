"""
n2k_command_builder.py — Reusable generator for NMEA 2000 PGN Commands.

Constructs NMEA 2000 frames using the nmea2000 library:
  - PGN 126208 (Group Function Command) to configure PGN 127505 (Fluid Level):
      • Field 1: Fluid Instance (0-15)
      • Field 2: Fluid Type (0-6)
      • Field 3: Tank Capacity (Liters)
  - PGN 126998 (Configuration Information) for Tank Name / Description.
  - PGN 59904 (ISO Request) to discover devices (ISO Address Claim 60928, Product Info 126996).
"""
import struct
from typing import Dict, Any, Optional

try:
    import nmea2000
    NMEA2000_AVAILABLE = True
except ImportError:
    NMEA2000_AVAILABLE = False


def build_iso_request_frame(requested_pgn: int, destination_address: int = 255) -> str:
    """
    Build ISO Request frame (PGN 59904) in RAW hex format for YDNU-02.
    Format: PGN 59904 payload is 3 bytes (little-endian 24-bit requested PGN).
    """
    # 24-bit PGN in little endian
    p0 = requested_pgn & 0xFF
    p1 = (requested_pgn >> 8) & 0xFF
    p2 = (requested_pgn >> 16) & 0xFF

    payload_hex = f"{p0:02X} {p1:02X} {p2:02X}"
    return payload_hex


def build_pgn_126208_command(
    target_address: int,
    instance: int,
    fluid_type_code: Optional[int] = None,
    capacity_l: Optional[float] = None,
    src_address: int = 200,
    target_pgn: int = 127505,
) -> Dict[str, Any]:
    """
    Build PGN 126208 (NMEA Command Group Function) payload.
    
    Generates 29-bit CAN ID string formatted for YDNU-02 RAW mode.
    Default target_pgn is 127505 (Fluid Level).
    """
    params = {
        "target_pgn": target_pgn,
        "target_address": target_address,
        "instance": instance,
        "fluid_type_code": fluid_type_code,
        "capacity_l": capacity_l,
    }

    # 29-bit CAN ID for PDU1 addressed PGN 126208 (0x1ED00)
    # Priority 6 (0x6), PDU Format 237 (0xED), Destination = target_address, Source = src_address
    can_id = (0x06 << 26) | (237 << 16) | ((target_address & 0xFF) << 8) | (src_address & 0xFF)
    can_id_hex = f"{can_id:08X}"

    payload_bytes = bytearray()
    payload_bytes.append(0x00)  # Command function
    payload_bytes.append(target_pgn & 0xFF)
    payload_bytes.append((target_pgn >> 8) & 0xFF)
    payload_bytes.append((target_pgn >> 16) & 0xFF)
    payload_bytes.append(0x08)  # Default priority

    num_params = 1  # Instance is always required
    if fluid_type_code is not None:
        num_params += 1
    if capacity_l is not None:
        num_params += 1

    payload_bytes.append(num_params)

    # Param 1: Instance (Field 1)
    payload_bytes.append(1)  # Field index 1 = Instance
    payload_bytes.append(instance & 0xFF)

    # Param 2: Fluid Type (Field 2)
    if fluid_type_code is not None:
        payload_bytes.append(2)  # Field index 2 = Fluid Type
        payload_bytes.append(fluid_type_code & 0xFF)

    # Param 3: Capacity (Field 3)
    if capacity_l is not None:
        payload_bytes.append(3)  # Field index 3 = Capacity
        cap_units = int(capacity_l * 10) & 0xFFFF
        payload_bytes.append(cap_units & 0xFF)
        payload_bytes.append((cap_units >> 8) & 0xFF)

    data_hex = " ".join(f"{b:02X}" for b in payload_bytes)
    raw_tx_str = f"{can_id_hex} {data_hex}"

    return {
        "pgn": 126208,
        "dst": target_address,
        "can_id_hex": can_id_hex,
        "hex_str": raw_tx_str,
        "bytes": bytes(payload_bytes),
        "params": params,
    }
