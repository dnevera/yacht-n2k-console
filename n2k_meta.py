"""
n2k_meta.py — Dynamic PGN Field Metadata & Config Protocol

Extracts field metadata from the nmea2000 library to build dynamic config UIs.
Handles PGN 126208 Read/Write Fields for device configuration.
"""
import re
import inspect
from typing import Dict, List, Any, Optional

try:
    from nmea2000 import NMEA2000Decoder, NMEA2000Encoder, pgns
    from nmea2000.consts import FieldTypes
    from nmea2000.message import NMEA2000Message, NMEA2000Field, IsoName
    from nmea2000.input_formats import N2KFormat
    _HAS_LIB = True
    _decoder = NMEA2000Decoder()
    _encoder = NMEA2000Encoder(output_format=N2KFormat.CAN_FRAME_ASCII_RAW)
except ImportError:
    _HAS_LIB = False
    _decoder = None
    _encoder = None

# Fields that are sensor measurements, not user-configurable
_READ_ONLY_FIELDS = frozenset({
    'level',              # Fluid Level — sensor reading
    'pressure',           # sensor reading
    'temperature',        # sensor reading
    'voltage',            # sensor reading
    'current',            # sensor reading
    'stateOfCharge',      # computed
    'timeRemaining',      # computed
    'sequenceCounter',    # internal
    'sid',                # Sequence ID
})


def _extract_lookup_keys(pgn: int) -> Dict[str, str]:
    """Extract lookup dict keys from PGN decode function source.

    Parses inspect.getsource(pgns.decode_pgn_XXXXX) to find patterns like:
      type = master_dict['TANK_TYPE'].get(type_raw, None)
    Returns mapping: {field_variable_name: master_dict_key}
    """
    if not _HAS_LIB:
        return {}

    func_name = f'decode_pgn_{pgn}'
    if not hasattr(pgns, func_name):
        return {}

    try:
        source = inspect.getsource(getattr(pgns, func_name))
    except Exception:
        return {}

    # Match: var_name = master_dict['KEY_NAME'].get(var_name_raw, None)
    pattern = r"(\w+)\s*=\s*master_dict\[[\'\"]([A-Z0-9_]+)[\'\"]\]\.get"
    return {var_name: dict_key for var_name, dict_key in re.findall(pattern, source)}


def get_pgn_field_metadata(pgn: int) -> List[Dict[str, Any]]:
    """Get metadata for all fields of a PGN.

    Returns list of dicts:
    - id: field identifier (e.g. 'instance', 'type')
    - name: human-readable name (e.g. 'Instance', 'Type')
    - type: 'number' | 'lookup' | 'string' | 'binary'
    - unit: unit of measurement or None
    - options: {raw_value: display_name} for lookup fields, else None
    - configurable: True if field can be written by user
    """
    if not _HAS_LIB or not _decoder:
        return []

    lookup_keys = _extract_lookup_keys(pgn)

    # Decode a dummy CAN frame to get field structure
    try:
        can_id = (6 << 26) | (pgn << 8)
        raw_str = f"{can_id:08X} " + " ".join(["00"] * 8)
        msg = _decoder.decode(raw_str)
        if not msg or not msg.fields:
            return []
    except Exception:
        return []

    result = []
    for field in msg.fields:
        fid = field.id

        # Skip reserved/spare/internal
        if fid.startswith('reserved') or fid.startswith('spare'):
            continue
        if fid == '##list##':
            continue

        is_configurable = fid not in _READ_ONLY_FIELDS

        field_type = 'number'
        options = None

        if field.type == FieldTypes.LOOKUP:
            field_type = 'lookup'
            dict_key = lookup_keys.get(fid)
            if dict_key and dict_key in pgns.master_dict:
                options = pgns.master_dict[dict_key]
        elif field.type == FieldTypes.INDIRECT_LOOKUP:
            field_type = 'lookup'
        elif field.type in (FieldTypes.STRING_FIX, FieldTypes.STRING_LZ, FieldTypes.STRING_LAU):
            field_type = 'string'
        elif field.type == FieldTypes.BINARY:
            field_type = 'binary'
            is_configurable = False

        result.append({
            'id': fid,
            'name': field.name,
            'type': field_type,
            'unit': field.unit_of_measurement,
            'options': options,
            'configurable': is_configurable,
        })

    return result


def get_pgn_name(pgn: int) -> str:
    """Get human-readable name for a PGN number."""
    if not _HAS_LIB or not _decoder:
        return f"PGN {pgn}"
    try:
        can_id = (6 << 26) | (pgn << 8)
        raw_str = f"{can_id:08X} 00 00 00 00 00 00 00 00"
        msg = _decoder.decode(raw_str)
        if msg and msg.description:
            return msg.description
    except Exception:
        pass
    return f"PGN {pgn}"


def build_iso_request_frame(requested_pgn: int, our_src: int = 16) -> str:
    """Build ISO Request (PGN 59904) as CAN_FRAME_ASCII_RAW string.

    Returns string like: '18EAFF10 00 EE 00'
    """
    # Manual construction — always reliable
    p0 = requested_pgn & 0xFF
    p1 = (requested_pgn >> 8) & 0xFF
    p2 = (requested_pgn >> 16) & 0xFF
    # PGN 59904 = 0xEA00, PDU Format 0xEA, broadcast to 0xFF
    can_id = (6 << 26) | (0xEA << 16) | (0xFF << 8) | (our_src & 0xFF)
    return f"{can_id:08X} {p0:02X} {p1:02X} {p2:02X}"


def build_read_fields_frame(target_src: int, target_pgn: int, our_src: int = 16) -> str:
    """Build PGN 126208 Read Fields Request as CAN_FRAME_ASCII_RAW string.

    Function code 3 = Read Fields Request.
    Requests ALL fields (numberOfSelectionPairs=0, numberOfParameters=0xFF).
    """
    # 29-bit CAN ID: Priority 3, PDU Format 0xED (PGN 126208), Destination = target_src
    can_id = (3 << 26) | (0xED << 16) | ((target_src & 0xFF) << 8) | (our_src & 0xFF)

    payload = bytearray()
    payload.append(3)  # Function Code: Read Fields
    # Target PGN (3 bytes LE)
    payload.append(target_pgn & 0xFF)
    payload.append((target_pgn >> 8) & 0xFF)
    payload.append((target_pgn >> 16) & 0xFF)
    # Manufacturer Code (11 bits) + Reserved (2 bits) + Industry Code (3 bits) = 0xFFFF for non-proprietary
    payload.append(0xFF)
    payload.append(0xFF)
    # Unique ID
    payload.append(0xFF)
    # Number of Selection Pairs (0 = select all instances)
    payload.append(0x00)
    # Number of Parameters to read (0xFF = all)
    payload.append(0xFF)

    hex_data = " ".join(f"{b:02X}" for b in payload)
    return f"{can_id:08X} {hex_data}"


def build_write_fields_frame(target_src: int, target_pgn: int,
                              field_pairs: List[tuple], our_src: int = 16) -> str:
    """Build PGN 126208 Write Fields as CAN_FRAME_ASCII_RAW string.

    Function code 5 = Write Fields.
    field_pairs: list of (field_index_1based, raw_value_bytes)
    """
    can_id = (3 << 26) | (0xED << 16) | ((target_src & 0xFF) << 8) | (our_src & 0xFF)

    payload = bytearray()
    payload.append(5)  # Function Code: Write Fields
    # Target PGN (3 bytes LE)
    payload.append(target_pgn & 0xFF)
    payload.append((target_pgn >> 8) & 0xFF)
    payload.append((target_pgn >> 16) & 0xFF)
    # Manufacturer Code + Reserved + Industry Code = 0xFFFF
    payload.append(0xFF)
    payload.append(0xFF)
    # Unique ID
    payload.append(0xFF)
    # Number of Selection Pairs
    payload.append(0x00)
    # Number of Parameters
    payload.append(len(field_pairs) & 0xFF)
    # Parameter pairs: field_number + value bytes
    for field_idx, value_bytes in field_pairs:
        payload.append(field_idx & 0xFF)
        payload.extend(value_bytes)

    hex_data = " ".join(f"{b:02X}" for b in payload)
    return f"{can_id:08X} {hex_data}"


def build_command_frame(target_src: int, target_pgn: int,
                         field_pairs: List[tuple], our_src: int = 16) -> str:
    """Build PGN 126208 Command Group Function as CAN_FRAME_ASCII_RAW string.

    Function code 1 = Command.
    field_pairs: list of (field_index_1based, raw_value_bytes)
    """
    can_id = (6 << 26) | (0xED << 16) | ((target_src & 0xFF) << 8) | (our_src & 0xFF)

    payload = bytearray()
    payload.append(1)  # Function Code: Command
    # Target PGN (3 bytes LE)
    payload.append(target_pgn & 0xFF)
    payload.append((target_pgn >> 8) & 0xFF)
    payload.append((target_pgn >> 16) & 0xFF)
    # Priority / Reserved
    payload.append(0x08)
    # Number of Parameters
    payload.append(len(field_pairs) & 0xFF)
    # Parameter pairs
    for field_idx, value_bytes in field_pairs:
        payload.append(field_idx & 0xFF)
        payload.extend(value_bytes)

    hex_data = " ".join(f"{b:02X}" for b in payload)
    return f"{can_id:08X} {hex_data}"


def parse_device_info(raw_line: str) -> Optional[Dict[str, Any]]:
    """Decode raw CAN frame and extract device info from PGN 60928 or 126996."""
    if not _HAS_LIB or not _decoder:
        return None
    try:
        msg = _decoder.decode(raw_line)
        if not msg:
            return None
    except Exception:
        return None

    info = {'pgn': msg.PGN, 'source': msg.source}

    if msg.PGN == 60928 and msg.source_iso_name:
        iso = msg.source_iso_name
        info['unique_number'] = iso.unique_number
        info['manufacturer'] = str(iso.manufacturer_code)
        info['device_function'] = str(iso.device_function)
        info['device_class'] = str(iso.device_class)
        info['device_instance'] = iso.device_instance
        info['industry_group'] = str(iso.industry_group)

    elif msg.PGN == 126996:
        for f in msg.fields:
            if f.value is not None:
                info[f.id] = str(f.value).strip() if isinstance(f.value, str) else f.value

    return info


def parse_pgn_list(raw_line: str) -> Optional[List[int]]:
    """Decode PGN 126464 and extract list of supported PGNs."""
    if not _HAS_LIB or not _decoder:
        return None
    try:
        msg = _decoder.decode(raw_line)
        if not msg or msg.PGN != 126464:
            return None
    except Exception:
        return None

    pgn_list = []
    for field in msg.fields:
        if field.id == '##list##' and isinstance(field.value, list):
            for entry in field.value:
                if 'pgn' in entry and isinstance(entry['pgn'].raw_value, int):
                    pgn_list.append(entry['pgn'].raw_value)
        elif field.id == 'pgn' and isinstance(field.raw_value, int):
            pgn_list.append(field.raw_value)

    return pgn_list


def decode_raw_line(raw_line: str) -> Optional[Dict[str, Any]]:
    """Decode a raw CAN frame line into a dict of field values."""
    if not _HAS_LIB or not _decoder:
        return None
    try:
        msg = _decoder.decode(raw_line)
        if not msg:
            return None
    except Exception:
        return None

    result = {
        'pgn': msg.PGN,
        'id': msg.id,
        'description': msg.description,
        'source': msg.source,
        'fields': {},
    }
    for f in msg.fields:
        if f.id.startswith('reserved') or f.id.startswith('spare') or f.id == '##list##':
            continue
        result['fields'][f.id] = {
            'value': f.value,
            'raw_value': f.raw_value,
            'unit': f.unit_of_measurement,
        }

    return result


if __name__ == '__main__':
    import json
    meta = get_pgn_field_metadata(127505)
    print("=== PGN 127505 Field Metadata ===")
    print(json.dumps(meta, indent=2))
    print()
    print("=== ISO Request Frame (PGN 60928) ===")
    print(build_iso_request_frame(60928))
    print()
    print("=== Read Fields Frame (SRC 92, PGN 127505) ===")
    print(build_read_fields_frame(92, 127505))
    print()
    print("=== Command Frame (SRC 92, PGN 127505, instance=0, type=1) ===")
    print(build_command_frame(92, 127505, [(1, bytes([0])), (2, bytes([1]))]))
