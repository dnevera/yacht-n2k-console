"""geo_location platform for ais_targets.

Turns raw per-field `nmea2000` entities carrying AIS PGN data into dynamic
`geo_location.ais_<mmsi>` entities, following the same structural pattern
HA core uses for `adsb`/`opensky`: a manager owns a periodic scan and adds/
removes `GeolocationEvent` entities as targets appear, update or go stale.

⚠️ ENTITY/ATTRIBUTE SHAPE CAVEAT
This sandboxed environment has no access to a live NMEA 2000 bus or a
running Home Assistant instance, so the exact entity_id/attribute layout
produced by the `nmea2000` integration for AIS PGNs could not be observed
directly — see the module docstring in `const.py` for the full reasoning.
The grouping/extraction logic below is written defensively: it matches on
entity_id substrings AND on an `mmsi` attribute if present, logs a warning
and skips gracefully whenever the expected shape is not found, and MUST be
re-verified against a live HA instance during deployment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    AIS_POSITION_ID_HINTS,
    AIS_STATIC_ID_HINTS,
    CONF_SCAN_INTERVAL,
    CONF_STALE_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
    GEO_LOCATION_SOURCE,
    MMSI_FIELD_SUFFIXES,
    NMEA2000_PLATFORM,
    POSITION_FIELD_SUFFIXES,
    STATIC_FIELD_SUFFIXES,
)

_LOGGER = logging.getLogger(__name__)

_ID_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_id(text: str) -> str:
    """Lowercase and strip separators so 'aisClassAPositionReport' and
    'ais_class_a_position_report' compare equal."""
    return _ID_NORMALIZE_RE.sub("", text.lower())


def _matches_any_hint(object_id: str, hints: tuple[str, ...]) -> bool:
    normalized = _normalize_id(object_id)
    return any(hint in normalized for hint in hints)


def _strip_known_suffix(
    object_id: str, suffix_map: dict[str, str]
) -> tuple[str, str] | None:
    """Return `(group_key, normalized_field)` when `object_id` ends with a
    known "_<field>" suffix from `suffix_map`, else `None`."""
    for suffix, normalized_field in suffix_map.items():
        marker = "_" + suffix
        if object_id.endswith(marker):
            return object_id[: -len(marker)], normalized_field
    return None


def _strip_mmsi_suffix(object_id: str) -> str | None:
    """Return the group key when `object_id` ends with a known MMSI suffix."""
    for suffix in MMSI_FIELD_SUFFIXES:
        marker = "_" + suffix
        if object_id.endswith(marker):
            return object_id[: -len(marker)]
    return None


@dataclass
class _TargetReading:
    """Aggregated reading for a single MMSI, merged across every distinct
    nmea2000 entity group that reported it (position PGNs + the static/
    voyage data PGN)."""

    mmsi: int
    latitude: float | None = None
    longitude: float | None = None
    sog: float | None = None
    cog: float | None = None
    heading: float | None = None
    nav_status: Any = None
    rate_of_turn: float | None = None
    vessel_name: str | None = None
    callsign: str | None = None
    ship_type: Any = None
    length: float | None = None
    beam: float | None = None
    destination: str | None = None
    eta: Any = None
    last_seen: datetime | None = None
    has_position: bool = False


def _iter_nmea2000_states(hass: HomeAssistant):
    """Yield every hass state that the entity registry attributes to the
    `nmea2000` platform (more reliable than guessing from entity_id alone).
    """
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        if entry.platform != NMEA2000_PLATFORM:
            continue
        state = hass.states.get(entry.entity_id)
        if state is not None:
            yield state


def _coerce_number(raw: Any) -> Any:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def _collect_readings(hass: HomeAssistant) -> dict[int, _TargetReading]:
    """Scan nmea2000 entities and build one aggregated `_TargetReading` per
    MMSI. Groups without a resolvable MMSI are logged and skipped."""
    position_groups: dict[str, dict[str, Any]] = {}
    static_groups: dict[str, dict[str, Any]] = {}
    mmsi_by_group: dict[str, int] = {}

    for state in _iter_nmea2000_states(hass):
        object_id = state.entity_id.split(".", 1)[1]

        is_position = _matches_any_hint(object_id, AIS_POSITION_ID_HINTS)
        is_static = _matches_any_hint(object_id, AIS_STATIC_ID_HINTS)
        if not is_position and not is_static:
            continue

        # An `mmsi` attribute, if the integration happens to expose one
        # directly on every sibling field's state, is the most reliable
        # grouping signal — prefer it whenever present.
        attr_mmsi = state.attributes.get("mmsi")

        mmsi_group_key = _strip_mmsi_suffix(object_id)
        if mmsi_group_key is not None:
            try:
                mmsi_by_group[mmsi_group_key] = int(float(state.state))
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "ais_targets: %s looks like an MMSI field but its state "
                    "(%r) is not numeric — skipping this group",
                    state.entity_id,
                    state.state,
                )
            continue

        if is_position:
            hit = _strip_known_suffix(object_id, POSITION_FIELD_SUFFIXES)
            if hit is None:
                _LOGGER.debug(
                    "ais_targets: %s matches an AIS position message id but "
                    "no known field suffix — ignoring (entity naming may "
                    "differ from what this integration assumes)",
                    state.entity_id,
                )
                continue
            group_key, field_name = hit
            group = position_groups.setdefault(group_key, {})
        else:
            hit = _strip_known_suffix(object_id, STATIC_FIELD_SUFFIXES)
            if hit is None:
                _LOGGER.debug(
                    "ais_targets: %s matches AIS static/voyage data but no "
                    "known field suffix — ignoring",
                    state.entity_id,
                )
                continue
            group_key, field_name = hit
            group = static_groups.setdefault(group_key, {})

        group[field_name] = _coerce_number(state.state)
        group["_last_seen"] = state.last_updated
        if attr_mmsi is not None:
            try:
                mmsi_by_group[group_key] = int(float(attr_mmsi))
            except (TypeError, ValueError):
                pass

    readings: dict[int, _TargetReading] = {}

    for group_key, fields in position_groups.items():
        mmsi = mmsi_by_group.get(group_key)
        if mmsi is None:
            _LOGGER.warning(
                "ais_targets: could not resolve MMSI for AIS position group "
                "'%s' (fields found: %s) — skipping. This likely means the "
                "real entity_id/attribute shape differs from what this "
                "integration assumes; see README.md and re-verify against "
                "a live HA instance.",
                group_key,
                sorted(fields),
            )
            continue
        if "latitude" not in fields or "longitude" not in fields:
            _LOGGER.debug(
                "ais_targets: MMSI %s position group '%s' has no lat/lon yet "
                "(fields so far: %s) — waiting for more data",
                mmsi,
                group_key,
                sorted(fields),
            )
            continue

        reading = readings.setdefault(mmsi, _TargetReading(mmsi=mmsi))
        reading.latitude = fields.get("latitude")
        reading.longitude = fields.get("longitude")
        reading.sog = fields.get("sog")
        reading.cog = fields.get("cog")
        reading.heading = fields.get("heading")
        reading.nav_status = fields.get("nav_status")
        reading.rate_of_turn = fields.get("rate_of_turn")
        reading.has_position = True
        last_seen = fields.get("_last_seen")
        if isinstance(last_seen, datetime):
            reading.last_seen = last_seen

    for group_key, fields in static_groups.items():
        mmsi = mmsi_by_group.get(group_key)
        if mmsi is None:
            _LOGGER.debug(
                "ais_targets: could not resolve MMSI for AIS static-data "
                "group '%s' (fields: %s) — skipping merge (any existing "
                "position-only entity for this vessel is unaffected)",
                group_key,
                sorted(fields),
            )
            continue

        # Static data must never block a position-only entity: merge into
        # whatever reading already exists (or create a position-less
        # placeholder so the detail card can still show identity fields
        # once a position report arrives later in the same scan cycle).
        reading = readings.setdefault(mmsi, _TargetReading(mmsi=mmsi))
        reading.vessel_name = fields.get("vessel_name", reading.vessel_name)
        reading.callsign = fields.get("callsign", reading.callsign)
        reading.ship_type = fields.get("ship_type", reading.ship_type)
        reading.length = fields.get("length", reading.length)
        reading.beam = fields.get("beam", reading.beam)
        reading.destination = fields.get("destination", reading.destination)
        reading.eta = fields.get("eta", reading.eta)
        last_seen = fields.get("_last_seen")
        if isinstance(last_seen, datetime) and (
            reading.last_seen is None or last_seen > reading.last_seen
        ):
            reading.last_seen = last_seen

    return readings


class AisTarget(GeolocationEvent):
    """A single AIS-tracked vessel, plotted via `geo_location_sources`."""

    _attr_should_poll = False
    _attr_source = GEO_LOCATION_SOURCE

    def __init__(self, reading: _TargetReading) -> None:
        self._reading = reading
        self._attr_unique_id = f"{DOMAIN}_{reading.mmsi}"
        self.entity_id = f"geo_location.ais_{reading.mmsi}"
        self._apply(reading)

    def _apply(self, reading: _TargetReading) -> None:
        self._reading = reading
        self._attr_name = reading.vessel_name or f"AIS {reading.mmsi}"
        self._attr_latitude = reading.latitude
        self._attr_longitude = reading.longitude

    def update_from_reading(self, reading: _TargetReading) -> None:
        """Refresh this entity in place from a newer scan's reading."""
        self._apply(reading)
        self.async_write_ha_state()

    @property
    def last_seen(self) -> datetime:
        return self._reading.last_seen or dt_util.utcnow()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self._reading
        last_seen = r.last_seen or dt_util.utcnow()
        return {
            "mmsi": r.mmsi,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "sog": r.sog,
            "cog": r.cog,
            "heading": r.heading,
            "nav_status": r.nav_status,
            "rate_of_turn": r.rate_of_turn,
            "vessel_name": r.vessel_name,
            "callsign": r.callsign,
            "ship_type": r.ship_type,
            "length": r.length,
            "beam": r.beam,
            "destination": r.destination,
            "eta": r.eta,
            "last_seen": last_seen.isoformat(),
        }


class AisTargetsManager:
    """Owns the periodic scan loop and the dynamic set of AisTarget entities.

    Structurally mirrors HA core's `adsb`/`opensky` geo_location platforms:
    one manager per config entry, ticking on `scan_interval` and expiring
    entities whose `last_seen` exceeds `stale_timeout`.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._async_add_entities = async_add_entities
        self._entities: dict[int, AisTarget] = {}
        self._unsub_interval = None

    @property
    def _options(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def scan_interval(self) -> int:
        return int(self._options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    @property
    def stale_timeout(self) -> timedelta:
        minutes = int(self._options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT))
        return timedelta(minutes=minutes)

    def async_start(self) -> None:
        """Start the periodic scan and run one scan immediately."""
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_scan, timedelta(seconds=self.scan_interval)
        )
        self.hass.async_create_task(self._async_scan_now())

    def async_stop(self) -> None:
        """Cancel the periodic scan (called on config entry unload)."""
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None

    async def _async_scan(self, _now: datetime) -> None:
        await self._async_scan_now()

    async def _async_scan_now(self) -> None:
        try:
            readings = _collect_readings(self.hass)
        except Exception:  # noqa: BLE001 - a scan glitch must never crash HA
            _LOGGER.exception("ais_targets: error scanning nmea2000 entities")
            return

        now = dt_util.utcnow()
        stale_before = now - self.stale_timeout

        new_entities: list[AisTarget] = []
        for mmsi, reading in readings.items():
            if reading.last_seen is None:
                reading.last_seen = now
            if reading.last_seen < stale_before:
                # Already stale by the time we saw it (e.g. a lingering
                # static-data-only group with no recent position) — do not
                # (re)create an entity for it.
                continue

            existing = self._entities.get(mmsi)
            if existing is None:
                entity = AisTarget(reading)
                self._entities[mmsi] = entity
                new_entities.append(entity)
            else:
                existing.update_from_reading(reading)

        if new_entities:
            self._async_add_entities(new_entities)

        for mmsi in list(self._entities):
            entity = self._entities[mmsi]
            if mmsi not in readings or entity.last_seen < stale_before:
                del self._entities[mmsi]
                self.hass.async_create_task(entity.async_remove(force_remove=True))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the geo_location platform for a config entry."""
    manager = AisTargetsManager(hass, entry, async_add_entities)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
    manager.async_start()
    entry.async_on_unload(manager.async_stop)
