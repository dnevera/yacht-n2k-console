"""device_contract.py — Unified N2K Device Registration Contract.
===================================================================

ARCHITECTURAL PRINCIPLES & NMEA 2000 DEVICE REGISTRATION SPECIFICATION:
-----------------------------------------------------------------------

1. ISO NAME SEPARATION (PGN 60928 / ISO Claim):
   - Every node on the NMEA 2000 CAN bus MUST possess a unique 64-bit ISO Name.
   - ISO Name contains: Unique Number (21-bit), Manufacturer Code (11-bit),
     Device Function (8-bit), Device Class (7-bit), Industry Group (3-bit).
   - Physical YDNU-02 USB Gateway: SA=64, Unique ID=402047, Mfg Code=717 (Yacht Devices).
   - Virtual TCP Gateway Service: SA=200, Unique ID=902047, Mfg Code=2047 (Custom).
   - Having distinct 21-bit Unique IDs ensures Home Assistant (ha-nmea2000) generates
     distinct device registry cards: 'Product Information (Yacht Devices - PC Gateway - 402047)'
     and 'Product Information (2047 - PC Gateway - 902047)'.

2. PRODUCT INFORMATION (PGN 126996) SOURCE ROUTING:
   - Product Information (PGN 126996) contains Model ID, Software Version Code,
     Model Serial Code, and Model Version.
   - To prevent state collisions in Home Assistant, every PGN 126996 message MUST be
     encoded with the EXACT Source Address (SA) of the device that owns it:
     • Physical YDNU-02 (SA=64): 29-bit CAN ID 0x19F01440 ('19F01440 ...')
     • Virtual TCP Gateway (SA=200): 29-bit CAN ID 0x19F014C8 ('19F014C8 ...')
   - When encoded with its distinct SA, Home Assistant binds the Model ID, S/N, and
     firmware version to the corresponding ISO Name, populating Product Info fields
     for physical YDNU-02 ('1.75 07/08/2025', '00402047') and TCP GW ('0.2.0', 'SW-GW-00902047').

3. UNIFORM USAGE OF NMEA2000 LIBRARY:
   - Message structures are built using `nmea2000.message.NMEA2000Message` and `NMEA2000Field`.
   - ASCII line construction is delegated to `nmea2000.encoder_formats.CanFrameAsciiEncoder`
     (the canonical library handler for N2KFormat.CAN_FRAME_ASCII).
   - Pure in-memory encoding: zero socket side effects, zero async dependencies.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from nmea2000.message import NMEA2000Message, NMEA2000Field
from nmea2000.encoder_formats import CanFrameAsciiEncoder
from nmea2000.decoder import NMEA2000Decoder


@dataclass
class N2KDeviceInfo:
    """Unified identity data model for any N2K device on the bus."""
    sa: int
    unique_id: int = 0
    mfg_code: int = 717
    device_class: int = 25
    device_function: int = 130
    industry_group: int = 4
    model_id: str = ""
    software_version: str = ""
    model_serial: str = ""
    model_version: str = ""
    active_pgns: List[int] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """Return True if Product Info (model + serial) has been populated."""
        return bool(self.model_id.strip() and self.model_serial.strip())


class N2KDeviceEncoder:
    """Encodes N2KDeviceInfo into canonical CAN_FRAME_ASCII byte lines.

    Uses CanFrameAsciiEncoder (from nmea2000 library) for pure in-memory message
    construction — zero socket connections, zero disk IO, zero async dependency,
    outputting standard 8-byte hex ASCII lines ('18EEFF40 7F 22...').
    """

    @classmethod
    def encode_announcement(cls, info: N2KDeviceInfo) -> List[bytes]:
        """Encode PGN 60928 and PGN 126996 into CAN_FRAME_ASCII byte lines."""
        if info.sa is None or not (0 <= info.sa <= 253):
            return []

        uid = info.unique_id or info.sa
        mfg = info.mfg_code or 717
        product_code = uid & 0xFFFF
        lines: List[bytes] = []
        encoder = CanFrameAsciiEncoder()

        try:
            # 1. PGN 60928 (ISO Address Claim, Destination=255 Broadcast)
            claim_msg = NMEA2000Message(PGN=60928, source=info.sa, destination=255, priority=6)
            claim_msg.fields = [
                NMEA2000Field(id='uniqueNumber', raw_value=uid),
                NMEA2000Field(id='manufacturerCode', raw_value=mfg),
                NMEA2000Field(id='deviceInstanceLower', raw_value=0),
                NMEA2000Field(id='deviceInstanceUpper', raw_value=0),
                NMEA2000Field(id='deviceFunction', raw_value=info.device_function or 130),
                NMEA2000Field(id='spare', raw_value=1),
                NMEA2000Field(id='deviceClass', raw_value=info.device_class or 25),
                NMEA2000Field(id='systemInstance', raw_value=0),
                NMEA2000Field(id='industryGroup', raw_value=info.industry_group or 4),
                NMEA2000Field(id='arbitraryAddressCapable', raw_value=1),
            ]
            lines.extend(encoder.encode(claim_msg))

            # 2. PGN 126996 (Product Information) if model/serial known
            if info.is_complete:
                prod_msg = NMEA2000Message(PGN=126996, source=info.sa, priority=6)
                prod_msg.fields = [
                    NMEA2000Field(id='nmea2000Version', value=2.1, raw_value=None),
                    NMEA2000Field(id='productCode', raw_value=product_code),
                    NMEA2000Field(id='modelId', value=info.model_id),
                    NMEA2000Field(id='softwareVersionCode', value=info.software_version or "1.0"),
                    NMEA2000Field(id='modelVersion', value=info.model_version or info.model_id),
                    NMEA2000Field(id='modelSerialCode', value=info.model_serial),
                    NMEA2000Field(id='certificationLevel', raw_value=1),
                    NMEA2000Field(id='loadEquivalency', raw_value=1),
                ]
                lines.extend(encoder.encode(prod_msg))

            return lines
        except Exception:
            return []


class N2KDeviceRegistry:
    """Thread-safe registry tracking all active N2K devices on the network."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._devices: Dict[int, N2KDeviceInfo] = {}
        self._decoder = NMEA2000Decoder()

    def update_from_frame(self, line: bytes) -> Optional[int]:
        """Decode a broadcast R-frame line and update device registry."""
        try:
            text = line.decode("ascii", errors="ignore").rstrip()
            msg = self._decoder.decode(text)
        except Exception:
            return None

        if msg is None or msg.source is None:
            return None

        sa = msg.source
        fields = {f.id: f.value for f in msg.fields}

        with self._lock:
            dev = self._devices.setdefault(sa, N2KDeviceInfo(sa=sa))

            if msg.PGN not in dev.active_pgns:
                dev.active_pgns.append(msg.PGN)

            if msg.PGN == 60928:
                uid = fields.get("uniqueNumber")
                if uid is not None:
                    dev.unique_id = int(uid)
                mfg = fields.get("manufacturerCode")
                if isinstance(mfg, (int, float)):
                    dev.mfg_code = int(mfg)
                dev.device_function = _coerce_int(fields.get("deviceFunction"), dev.device_function)
                dev.industry_group = _coerce_int(fields.get("industryGroup"), dev.industry_group)
                dev.device_class = _coerce_int(fields.get("deviceClass"), dev.device_class)
                return sa

            elif msg.PGN == 126996:
                m_id = fields.get("modelId")
                if m_id:
                    dev.model_id = str(m_id).strip()
                s_ver = fields.get("softwareVersionCode")
                if s_ver:
                    dev.software_version = str(s_ver).strip()
                m_ser = fields.get("modelSerialCode")
                if m_ser:
                    dev.model_serial = str(m_ser).strip()
                m_ver = fields.get("modelVersion")
                if m_ver:
                    dev.model_version = str(m_ver).strip()
                return sa

        return None

    def register_device(self, info: N2KDeviceInfo) -> None:
        """Register or update an explicit device definition."""
        with self._lock:
            self._devices[info.sa] = info

    def get_device(self, sa: int) -> Optional[N2KDeviceInfo]:
        """Return a copy of N2KDeviceInfo for a given SA, or None."""
        with self._lock:
            dev = self._devices.get(sa)
            if dev:
                return N2KDeviceInfo(**dev.__dict__)
            return None

    def get_all_devices(self) -> Dict[int, N2KDeviceInfo]:
        """Return a snapshot dictionary of all registered devices."""
        with self._lock:
            return {sa: N2KDeviceInfo(**dev.__dict__) for sa, dev in self._devices.items()}

    def generate_all_announcements(self) -> List[bytes]:
        """Generate ASCII broadcast lines for all tracked devices."""
        snapshot = self.get_all_devices()
        lines: List[bytes] = []
        for sa, dev in snapshot.items():
            lines.extend(N2KDeviceEncoder.encode_announcement(dev))
        return lines


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    return default
