"""NMEA 2000 PGN Decoder module for YDNU-02.

Decodes CAN IDs, ISO Address Claims (60928), Product Info (126996),
and leverages the optional `nmea2000` library for deep decoding.
"""

from typing import Optional, Dict, Any

try:
    from nmea2000 import NMEA2000Decoder as _N2KDecoder
    from nmea2000.consts import IndirectLookupEncodeMaps as _N2KMaps
    _n2k_decoder = _N2KDecoder()
    _DEVICE_FUNC_REVERSE: Dict[tuple, str] = {}
    for _cls, _funcs in _N2KMaps.get("DEVICE_FUNCTION", {}).items():
        for _name, _code in _funcs.items():
            _DEVICE_FUNC_REVERSE[(_cls, _code)] = _name
    _HAS_N2K_LIB = True
except ImportError:
    _n2k_decoder = None
    _DEVICE_FUNC_REVERSE = {}
    _HAS_N2K_LIB = False


try:
    from nmea2000.consts import ManufacturerCodes as _MFR_CODES
except ImportError:
    _MFR_CODES = []


def resolve_manufacturer_name(mfg_code: Any) -> str:
    """Dynamically resolve NMEA 2000 manufacturer name without hardcoded dicts.

    Uses standard nmea2000 library ManufacturerCodes list.
    0x7FF (2047) is the NMEA 2000 standard reserved/custom manufacturer code.
    """
    try:
        code = int(mfg_code)
    except (ValueError, TypeError):
        return str(mfg_code) or "Unknown"

    if 0 <= code < len(_MFR_CODES) and _MFR_CODES[code]:
        return str(_MFR_CODES[code])
    if code == 2047:
        return "Custom / Reserved (2047)"
    return f"MfgCode {code}"


class N2KPGNDecoder:
    """Static PGN decoder for CAN frames."""

    @classmethod
    def parse_device_info(cls, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """Parse PGN 60928 / 126996 from parse_raw_line() result into structured device info."""
        pgn = parsed.get("info", {}).get("pgn", 0)
        data = parsed.get("data", b"")
        raw_line = parsed.get("raw", "")
        result = {}

        if pgn == 60928 and len(data) >= 8:
            lib_msg = cls._decode_via_lib(parsed)
            if lib_msg and lib_msg.source_iso_name:
                iso = lib_msg.source_iso_name
                result["unique_id"] = iso.unique_number
                result["mfg_code"] = iso.manufacturer_code
                result["function"] = iso.device_function
                result["device_class"] = iso.device_class
                result["device_class_name"] = str(iso.device_class)
                result["function_name"] = str(iso.device_function)
                mfr_field = lib_msg.get_field_by_id("manufacturerCode")
                if mfr_field and mfr_field.value and not str(mfr_field.value).isdigit():
                    result["manufacturer"] = str(mfr_field.value)
                else:
                    result["manufacturer"] = resolve_manufacturer_name(iso.manufacturer_code)
            else:
                val = int.from_bytes(data[:8], 'little')
                result["unique_id"] = val & 0x1FFFFF
                result["mfg_code"] = (val >> 21) & 0x7FF
                result["function"] = (val >> 40) & 0xFF
                result["device_class"] = (val >> 49) & 0x7F
                result["device_class_name"] = cls._class_name(result["device_class"])
                result["function_name"] = _DEVICE_FUNC_REVERSE.get(
                    (result["device_class"], result["function"]),
                    f"Function {result['function']}"
                )
                result["manufacturer"] = resolve_manufacturer_name(result["mfg_code"])

        elif pgn == 126996 and len(data) >= 36:
            lib_msg = cls._decode_via_lib(parsed)
            if lib_msg:
                fields = {f.id: f for f in lib_msg.fields}
                if "modelId" in fields and fields["modelId"].value:
                    result["model"] = str(fields["modelId"].value).strip()
                if "softwareVersionCode" in fields and fields["softwareVersionCode"].value:
                    result["firmware"] = str(fields["softwareVersionCode"].value).strip()
                if "modelVersion" in fields and fields["modelVersion"].value:
                    result["model_version"] = str(fields["modelVersion"].value).strip()
                if "modelSerialCode" in fields and fields["modelSerialCode"].value:
                    result["serial"] = str(fields["modelSerialCode"].value).strip()
                pc = fields.get("nmea2000DatabaseVersion") or fields.get("nmea2000CertificationLevel")
                if pc:
                    result["product_code"] = pc.raw_value
            else:
                result["product_code"] = int.from_bytes(data[2:4], 'little')
                def _extract(start, end):
                    if len(data) > start:
                        chunk = data[start:min(end, len(data))]
                        return chunk.split(b"\x00")[0].decode("ascii", errors="ignore").strip("\xff ")
                    return ""
                model = _extract(4, 36)
                if model: result["model"] = model
                fw = _extract(36, 68)
                if fw: result["firmware"] = fw
                mv = _extract(68, 100)
                if mv: result["model_version"] = mv
                sn = _extract(100, 132)
                if sn: result["serial"] = sn

        return result

    @staticmethod
    def _class_name(dev_class: int) -> str:
        """Resolve device class code to human-readable name."""
        _DEVICE_CLASS_NAMES = {
            0: "Reserved", 10: "System Tools", 20: "Safety Systems",
            25: "Inter/Intranetwork Device", 30: "Electrical Distribution",
            35: "Electrical Generation", 40: "Steering and Control",
            50: "Propulsion", 60: "Navigation", 70: "Communication",
            75: "Sensor Communication Interface", 80: "Instrumentation",
            85: "External Environment", 90: "Internal Environment",
        }
        return _DEVICE_CLASS_NAMES.get(dev_class, f"Class {dev_class}")

    @staticmethod
    def parse_can_id(can_id_hex: str) -> Dict[str, int]:
        """Parse a 29-bit CAN ID into NMEA 2000 components (PGN, Source, Destination, Priority)."""
        can_id = int(can_id_hex, 16)
        priority = (can_id >> 26) & 0x7
        pgn_raw = (can_id >> 8) & 0x3FFFF
        src = can_id & 0xFF

        pdu_format = (pgn_raw >> 8) & 0xFF
        pdu_specific = pgn_raw & 0xFF
        if pdu_format < 240:
            dst = pdu_specific
            pgn = pgn_raw & 0x3FF00
        else:
            dst = 255
            pgn = pgn_raw

        return {"can_id": can_id, "priority": priority, "pgn": pgn, "src": src, "dst": dst}

    @staticmethod
    def pgn_name(pgn: int) -> str:
        """Get human-readable name for a PGN number. Uses nmea2000 library."""
        if _HAS_N2K_LIB and _n2k_decoder:
            try:
                can_id = (6 << 26) | (pgn << 8)
                raw_str = f"{can_id:08X} 00 00 00 00 00 00 00 00"
                msg = _n2k_decoder.decode(raw_str)
                if msg and msg.description:
                    return msg.description
            except Exception:
                pass
        return f"PGN {pgn}"

    @classmethod
    def decode_pgn(cls, pgn: int, src: int, data: bytes) -> str:
        """Decode a PGN payload into a human-readable string."""
        # FastPacket PGNs (multi-frame): NEVER feed sub-frames to the shared
        # _n2k_decoder here — doing so poisons the sequence counter and
        # prevents feed_to_lib() from assembling the complete packet.
        # Assembly is handled exclusively by feed_to_lib() in SensorRegistry.
        _FAST_PACKET_PGNS = {126996, 126998, 129029, 129540, 130567, 130577}
        if pgn in _FAST_PACKET_PGNS:
            return f"[PGN {pgn}] Src:{src} Data:{data.hex(' ').upper()}"

        if _HAS_N2K_LIB and _n2k_decoder:
            try:
                # Build a synthetic CAN ID accepted by the nmea2000 library decoder.
                # Priority=6 is a safe default; PGN occupies bits 25-8; src in bits 7-0.
                can_id = (6 << 26) | (pgn << 8) | src
                raw_str = f"{can_id:08X} " + " ".join(f"{b:02X}" for b in data)
                msg = _n2k_decoder.decode(raw_str)
                if msg:
                    parts = [f"[PGN {pgn} {msg.description}] Src:{src}"]
                    for f in msg.fields:
                        if f.id.startswith("reserved") or f.id.startswith("spare"):
                            continue
                        val = f.value if f.value is not None else f.raw_value
                        parts.append(f"{f.name}:{val}")
                    return " ".join(parts)
            except Exception:
                pass

        return f"[PGN {pgn}] Src:{src} Data:{data.hex(' ').upper()}"

    @classmethod
    def _decode_via_lib(cls, parsed: Dict[str, Any]) -> Any:
        """Decode a parsed CAN frame using the nmea2000 library."""
        if not _HAS_N2K_LIB or not _n2k_decoder:
            return None
        try:
            info = parsed.get("info", {})
            data = parsed.get("data", b"")
            can_id = info.get("can_id", 0)
            raw_str = f"{can_id:08X} " + " ".join(f"{b:02X}" for b in data)
            return _n2k_decoder.decode(raw_str)
        except Exception:
            return None

    @classmethod
    def feed_to_lib(cls, parsed: Dict[str, Any]) -> Any:
        """Feed any CAN frame to the library decoder (enables fast-packet reassembly)."""
        if not _HAS_N2K_LIB or not _n2k_decoder:
            return None
        try:
            info = parsed.get("info", {})
            data = parsed.get("data", b"")
            can_id = info.get("can_id")

            if can_id is not None and data:
                raw_str = f"{can_id:08X} " + " ".join(f"{b:02X}" for b in data)
                return _n2k_decoder.decode(raw_str)

            raw_line = parsed.get("raw", "")
            if raw_line:
                # Strip timestamp and direction prefix if present ("00:00:00.000 R ")
                clean_line = normalize_frame(raw_line.encode("ascii", "ignore")).decode("ascii", "ignore").strip()
                return _n2k_decoder.decode(clean_line)

            return None
        except Exception:
            return None

    @classmethod
    def parse_raw_line(cls, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single RAW CAN line from the YDNU-02 device."""
        parts = line.strip().split()
        if len(parts) < 4:
            return None
        if parts[1] not in ('R', 'T'):
            return None
        try:
            can_id_hex = parts[2]
            info = cls.parse_can_id(can_id_hex)
            data_bytes = bytes(int(b, 16) for b in parts[3:])
            return {
                "raw": line.strip(),
                "time": parts[0],
                "dir": parts[1],
                "can_id_hex": can_id_hex,
                "info": info,
                "data": data_bytes,
                "decoded": cls.decode_pgn(info["pgn"], info["src"], data_bytes),
            }
        except (ValueError, IndexError):
            return None
