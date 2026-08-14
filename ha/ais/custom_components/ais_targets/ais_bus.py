"""Direct YDNU-02 gateway reader + in-memory AIS decoder.

This is the heart of the re-architected `ais_targets` integration. Instead of
piggy-backing on the `nmea2000` HA integration's entities (which pollute the
HA registry with a throwaway device per passing MMSI), we open a plain TCP
socket to the YDNU-02 gateway and decode AIS PGNs ourselves, entirely in RAM.

Frame format (identical to ydnu02/pgn_decoder.py's RAW parser):

    ``HH:MM:SS.mmm R <CANID_HEX> <DATA_BYTE_HEX>...``

Only AIS PGNs are decoded (every other frame is dropped after a cheap CAN-ID
parse), so the CPU cost stays negligible even on a busy bus. Multi-frame
(FastPacket) AIS messages are reassembled by the stateful
`nmea2000.NMEA2000Decoder` — we feed it only AIS frames, and since every frame
of a given FastPacket message shares the same CAN-ID (PGN+source), filtering
by PGN never breaks reassembly.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    AIS_POSITION_PGNS,
    AIS_STATIC_PGNS,
    FIELD_MMSI,
    OWN_POSITION_PGNS,
    PGN_GNSS_POSITION_DATA,
)

_LOGGER = logging.getLogger(__name__)

# All PGNs we bother to fully decode: AIS targets (position + static/voyage
# data) plus OUR OWN GNSS fix, which is used as the distance origin.
_DECODE_PGNS = AIS_POSITION_PGNS | AIS_STATIC_PGNS | OWN_POSITION_PGNS

_MS_TO_KNOTS = 1.9438444924406046

# Reconnect backoff bounds (seconds).
_RECONNECT_MIN = 2
_RECONNECT_MAX = 30


@dataclass
class AisTargetReading:
    """Mutable, in-memory record for a single tracked vessel (by MMSI).

    Position fields are refreshed by 129038/129039/129040; the identity/voyage
    fields are merged in from 129794/129809/129810 whenever they arrive and are
    otherwise left as ``None`` (static data must never block a position-only
    target from being plotted).
    """

    mmsi: int
    latitude: float | None = None
    longitude: float | None = None
    sog: float | None = None          # knots
    cog: float | None = None          # degrees 0..360
    heading: float | None = None      # degrees 0..360
    nav_status: str | None = None
    rate_of_turn: float | None = None  # deg/min
    vessel_name: str | None = None
    callsign: str | None = None
    ship_type: str | None = None
    length: float | None = None       # metres
    beam: float | None = None         # metres
    destination: str | None = None
    eta: str | None = None
    last_seen: datetime | None = None

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None


def parse_can_id(can_id_hex: str) -> tuple[int, int, int]:
    """Return (can_id, pgn, source) from a 29-bit CAN-ID hex string.

    Same decode as ydnu02/pgn_decoder.py's ``parse_can_id``.
    """
    can_id = int(can_id_hex, 16)
    pgn_raw = (can_id >> 8) & 0x3FFFF
    src = can_id & 0xFF
    pdu_format = (pgn_raw >> 8) & 0xFF
    if pdu_format < 240:
        pgn = pgn_raw & 0x3FF00
    else:
        pgn = pgn_raw
    return can_id, pgn, src


def _clean_str(value: Any) -> str | None:
    """Strip AIS string padding (NULs, '@', trailing spaces) → None if empty."""
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\x00", "").replace("@", "").strip()
    return cleaned or None


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class AisBusClient:
    """Owns the gateway socket, the decoder, and the per-MMSI target table."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._targets: dict[int, AisTargetReading] = {}
        # Identity/voyage data (129794/129809/129810) is broadcast only every
        # few MINUTES, while position reports arrive every few seconds. Keeping
        # the static fields only on the (expirable) target record meant that
        # every target expiry threw the vessel's name/callsign/type/size away,
        # so a re-appearing vessel showed up as a bare "AIS <mmsi>" row with
        # empty columns for minutes on end — which is exactly what the live
        # dashboard looked like. This cache is keyed by MMSI, survives
        # drop()/expiry, and is replayed onto any (re-)created target.
        self._static_cache: dict[int, dict[str, Any]] = {}
        # Our own boat's position, decoded straight off the bus from the GNSS
        # receiver's own PGNs (129029/129025). This is the authoritative origin
        # for the target-distance column — no HA template sensor involved.
        self.own_position: tuple[float, float] | None = None
        self.own_position_at: datetime | None = None
        self._own_position_pgn: int | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._decoder = None  # created lazily on the executor (imports nmea2000)
        self.connected = False
        self.last_error: str | None = None

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> None:
        self._stop.clear()
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def snapshot(self) -> dict[int, AisTargetReading]:
        """Return a shallow copy of the current target table for the platform."""
        return dict(self._targets)

    def drop(self, mmsi: int) -> None:
        """Remove an expired target from the in-memory table.

        The static/identity cache is deliberately KEPT so that a vessel coming
        back into range is immediately named again instead of waiting minutes
        for the next 129794/129809/129810 broadcast.
        """
        self._targets.pop(mmsi, None)

    # ── socket loop ─────────────────────────────────────────────────────────
    async def _run(self) -> None:
        backoff = _RECONNECT_MIN
        # Import + construct the decoder off the event loop (it loads the large
        # nmea2000 PGN tables). Done once; the decoder is stateful for
        # FastPacket reassembly so it must be reused across frames.
        try:
            self._decoder = await asyncio.get_event_loop().run_in_executor(
                None, self._make_decoder
            )
        except Exception as err:  # noqa: BLE001
            self.last_error = f"nmea2000 import failed: {err}"
            _LOGGER.error(
                "ais_targets: could not initialise the nmea2000 decoder (%s); "
                "the nmea2000 library must be installed in this HA environment",
                err,
            )
            return

        while not self._stop.is_set():
            try:
                await self._connect_and_read()
                backoff = _RECONNECT_MIN
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                self.connected = False
                self.last_error = str(err)
                _LOGGER.warning(
                    "ais_targets: gateway %s:%s read error (%s); reconnecting in %ss",
                    self._host,
                    self._port,
                    err,
                    backoff,
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, _RECONNECT_MAX)

    @staticmethod
    def _make_decoder():
        from nmea2000 import NMEA2000Decoder  # local import: heavy module

        return NMEA2000Decoder()

    async def _connect_and_read(self) -> None:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        self.connected = True
        self.last_error = None
        _LOGGER.info("ais_targets: connected to gateway %s:%s", self._host, self._port)
        try:
            while not self._stop.is_set():
                line = await reader.readline()
                if not line:
                    raise ConnectionError("gateway closed the connection (EOF)")
                self._handle_line(line)
        finally:
            self.connected = False
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    def _handle_line(self, raw: bytes) -> None:
        parts = raw.decode("ascii", "ignore").strip().split()
        if len(parts) < 4 or parts[1] not in ("R", "T"):
            return
        try:
            can_id, pgn, _src = parse_can_id(parts[2])
        except (ValueError, IndexError):
            return
        if pgn not in _DECODE_PGNS:
            return
        try:
            data_bytes = bytes(int(b, 16) for b in parts[3:])
        except ValueError:
            return
        raw_str = f"{can_id:08X} " + " ".join(f"{b:02X}" for b in data_bytes)
        try:
            msg = self._decoder.decode(raw_str)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("ais_targets: decode failed for PGN %s: %s", pgn, err)
            return
        if msg is None:
            # Incomplete FastPacket frame — the decoder will emit the assembled
            # message on a later frame.
            return
        if pgn in OWN_POSITION_PGNS:
            self._ingest_own_position(msg, pgn)
            return
        self._ingest(msg)

    # ── our own GNSS fix ────────────────────────────────────────────────────
    def _ingest_own_position(self, msg: Any, pgn: int) -> None:
        """Record our own boat's position from a GNSS PGN.

        129029 (full GNSS position data) wins over 129025 (rapid update): it is
        the receiver's complete fix. Once 129029 has been seen we ignore 129025
        so a second, less capable source cannot flip-flop the origin.
        """
        fields = {f.id: f for f in msg.fields}

        def val(fid: str) -> Any:
            f = fields.get(fid)
            return getattr(f, "value", None) if f else None

        lat = _num(val("latitude"))
        lon = _num(val("longitude"))
        if lat is None or lon is None:
            return
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return
        # A partially-filled fix (either coordinate sitting exactly on zero) is
        # not a position: trusting one on prod put the boat 1500 km away.
        if abs(lat) < 0.001 or abs(lon) < 0.001:
            return
        if (
            pgn != PGN_GNSS_POSITION_DATA
            and self._own_position_pgn == PGN_GNSS_POSITION_DATA
        ):
            return
        self.own_position = (round(lat, 6), round(lon, 6))
        self.own_position_at = dt_util.utcnow()
        self._own_position_pgn = pgn

    # ── decode → target table ───────────────────────────────────────────────
    def _ingest(self, msg: Any) -> None:
        fields = {f.id: f for f in msg.fields}
        mmsi_field = fields.get(FIELD_MMSI)
        mmsi = getattr(mmsi_field, "value", None) if mmsi_field else None
        try:
            mmsi = int(mmsi)
        except (TypeError, ValueError):
            return
        if mmsi <= 0:
            return

        target = self._targets.get(mmsi)
        if target is None:
            target = AisTargetReading(mmsi=mmsi)
            self._targets[mmsi] = target
            # Re-apply anything we already learned about this MMSI earlier.
            for attr, value in self._static_cache.get(mmsi, {}).items():
                setattr(target, attr, value)

        pgn = getattr(msg, "PGN", None)
        if pgn in AIS_POSITION_PGNS:
            self._apply_position(target, fields)
        # 129040 (Class B extended position) also carries some static fields.
        if pgn in AIS_STATIC_PGNS or pgn == 129040:
            self._apply_static(target, fields)

    def _apply_position(self, target: AisTargetReading, fields: dict) -> None:
        def val(fid: str) -> Any:
            f = fields.get(fid)
            return getattr(f, "value", None) if f else None

        lat = _num(val("latitude"))
        lon = _num(val("longitude"))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            target.latitude = round(lat, 6)
            target.longitude = round(lon, 6)

        sog = _num(val("sog"))
        if sog is not None:
            target.sog = round(sog * _MS_TO_KNOTS, 1)

        cog = _num(val("cog"))
        if cog is not None:
            target.cog = round(math.degrees(cog) % 360, 1)

        hdg = val("heading")
        if hdg is None:
            hdg = val("trueHeading")
        hdg = _num(hdg)
        if hdg is not None:
            target.heading = round(math.degrees(hdg) % 360, 1)

        rot = _num(val("rateOfTurn"))
        if rot is not None:
            target.rate_of_turn = round(math.degrees(rot) * 60, 1)

        nav = val("navStatus")
        if nav is not None:
            target.nav_status = str(nav)

        target.last_seen = dt_util.utcnow()

    def _apply_static(self, target: AisTargetReading, fields: dict) -> None:
        def val(fid: str) -> Any:
            f = fields.get(fid)
            return getattr(f, "value", None) if f else None

        learned: dict[str, Any] = {}

        def remember(attr: str, value: Any) -> None:
            if value is None:
                return
            setattr(target, attr, value)
            learned[attr] = value

        remember("vessel_name", _clean_str(val("name")))
        remember("callsign", _clean_str(val("callsign")))
        ship_type = val("typeOfShip")
        remember("ship_type", str(ship_type) if ship_type is not None else None)
        length = _num(val("length"))
        remember("length", round(length, 1) if length is not None else None)
        beam = _num(val("beam"))
        remember("beam", round(beam, 1) if beam is not None else None)
        remember("destination", _clean_str(val("destination")))
        remember("eta", self._format_eta(val("etaDate"), val("etaTime")))

        # Keep the identity fields around across target expiry (see
        # `_static_cache` in __init__): these PGNs repeat only every few
        # minutes, so re-learning them from scratch would leave the table
        # full of nameless "AIS <mmsi>" rows.
        if learned:
            self._static_cache.setdefault(target.mmsi, {}).update(learned)
        # A static-only message shouldn't be able to keep a never-positioned
        # target "alive" forever, so only bump last_seen once we have a fix.
        if target.has_position and target.last_seen is None:
            target.last_seen = dt_util.utcnow()

    @staticmethod
    def _format_eta(eta_date: Any, eta_time: Any) -> str | None:
        """Combine the AIS ETA date/time fields into an ISO-ish string.

        The library returns etaDate as a date (or day-count) and etaTime as a
        time/seconds value; we present whatever is available as a plain string
        rather than risk a wrong absolute timestamp.
        """
        date_str = _clean_str(str(eta_date)) if eta_date is not None else None
        time_str = _clean_str(str(eta_time)) if eta_time is not None else None
        if date_str and time_str:
            return f"{date_str} {time_str}"
        return date_str or time_str
