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
     distinct device registry cards:
       • 'Product Information (Yacht Devices - PC Gateway - 402047)'
       • 'Product Information (2047 - PC Gateway - 902047)'

2. FASTPACKET ASSEMBLY & DECODER NORMALIZATION:
   - Product Information (PGN 126996) is transmitted as a 19-frame FastPacket sequence (C0..D3).
   - Physical YDNU-02 hardware emits these frames with direction flag 'T'.
   - `N2KDeviceRegistry.update_from_frame()` uses `normalize_frame()` to convert 'T' to 'R'
     so `NMEA2000Decoder` successfully assembles the 19 frames and sets `is_complete = True`
     (populating `model_id` and `model_serial`).

3. PRE-REGISTRATION GUARANTEE (`DEFAULT_PHYSICAL_DEVICE` + `DEFAULT_VIRTUAL_DEVICE`):
   - To prevent race conditions where Home Assistant connects before FastPacket assembly completes,
     both `DEFAULT_PHYSICAL_DEVICE` (SA=64) and `DEFAULT_VIRTUAL_DEVICE` (SA=200) are pre-registered
     in `DataHub.__init__`.
   - This guarantees that `DataHub.announce_all_devices()` immediately transmits PGN 60928 and PGN 126996
     for BOTH devices as soon as a TCP client connects.

DIAGNOSTIC SKILL / MINI-PROMPTS:
================================
  Skill — verify default device pre-registration::

      python3 -c "
      from ydnu02_tcp_gateway.device_contract import DEFAULT_PHYSICAL_DEVICE, DEFAULT_VIRTUAL_DEVICE
      assert DEFAULT_PHYSICAL_DEVICE.unique_id == 402047
      assert DEFAULT_VIRTUAL_DEVICE.unique_id == 902047
      print('Physical:', DEFAULT_PHYSICAL_DEVICE)
      print('Virtual:', DEFAULT_VIRTUAL_DEVICE)
      "

  Skill — verify FastPacket PGN 126996 assembly in N2KDeviceRegistry::

      python3 -c "
      from ydnu02_tcp_gateway.device_contract import N2KDeviceRegistry
      reg = N2KDeviceRegistry()
      lines = [
          b'00:00:00.000 R 18EEFF40 7F 22 A6 59 00 82 32 C0\\n',
          b'00:00:00.000 R 19F01440 C0 86 15 05 83 19 59 44\\n',
          b'00:00:00.000 R 19F01440 D3 01 01\\n',
      ]
      for line in lines: reg.update_from_frame(line)
      print('Registered devices:', reg.get_all_devices())
      "
"""

import sys
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


DEFAULT_PHYSICAL_DEVICE = N2KDeviceInfo(
    sa=64,
    unique_id=402047,
    mfg_code=717,
    device_class=25,
    device_function=130,
    industry_group=4,
    model_id="YDNU-02",
    software_version="1.75 07/08/2025",
    model_serial="00402047",
    model_version="NMEA 2000 USB Gateway",
)

DEFAULT_VIRTUAL_DEVICE = N2KDeviceInfo(
    sa=200,
    unique_id=902047,
    mfg_code=2047,
    device_class=25,
    device_function=130,
    industry_group=4,
    model_id="YDNU-02 TCP-GW",
    software_version="0.2.0",
    model_serial="SW-GW-00902047",
    model_version="yacht-n2k-console",
)


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
        except Exception as e:
            sys.stderr.write(f"[device_contract] encode error: {e}\n")
            return []


from ydnu02_tcp_gateway.frame_utils import normalize_frame


class N2KDeviceRegistry:
    """Thread-safe registry tracking all active N2K devices on the network."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._devices: Dict[int, N2KDeviceInfo] = {}
        self._decoder = NMEA2000Decoder()

    def update_from_frame(self, line: bytes) -> Optional[int]:
        """Decode a broadcast R-frame line and update device registry."""
        try:
            norm = normalize_frame(line)
            text = norm.decode("ascii", errors="ignore").rstrip()
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
        """Register or update an explicit device definition (stores a defensive copy)."""
        with self._lock:
            self._devices[info.sa] = N2KDeviceInfo(**info.__dict__)

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
