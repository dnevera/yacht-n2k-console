"""geo_location platform for ais_targets.

Turns the in-memory per-MMSI AIS target table maintained by `AisBusClient`
(which reads and decodes the raw gateway stream directly — see ais_bus.py)
into dynamic `geo_location.ais_<mmsi>` entities, following the same structural
pattern HA core uses for `adsb`/`opensky`: a manager owns a periodic refresh
and adds/removes `GeolocationEvent` entities as targets appear, update or go
stale.

Nothing here touches the `nmea2000` HA integration or its entities — the AIS
data comes straight off the gateway socket, so HA's device/entity registry is
never polluted with transient per-MMSI devices.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.util.location import distance

from .ais_bus import AisBusClient, AisTargetReading
from .const import (
    CONF_OWN_BEAM,
    CONF_OWN_CALLSIGN,
    CONF_OWN_LENGTH,
    CONF_OWN_MMSI,
    CONF_OWN_NAME,
    CONF_OWN_SHIP_TYPE,
    CONF_STALE_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_OWN_MMSI,
    DEFAULT_STALE_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    GEO_LOCATION_SOURCE,
)

_LOGGER = logging.getLogger(__name__)

# Our own boat stays an ordinary target row: same entity_id scheme, same
# source, same columns. It is marked ONLY by this icon prefix on the name (no
# "Own Boat" caption), and only when the decoded MMSI equals the `own_mmsi`
# configured on the integration.
_OWN_BOAT_ICON = "⛵"



def _valid_origin(lat: Any, lon: Any) -> tuple[float, float] | None:
    """Sanity-check a candidate distance origin.

    A null-island / partially-filled fix (e.g. HA's GPS tracker reporting
    latitude 42.43 with longitude 0.0, seen live on prod) is not a position —
    using it made every AIS target read ~1500 km away and dropped a marker in
    the middle of nowhere. Such values are rejected outright.
    """
    try:
        flat = float(lat)
        flon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= flat <= 90) or not (-180 <= flon <= 180):
        return None
    if abs(flat) < 0.001 or abs(flon) < 0.001:
        return None
    return (flat, flon)


class AisTarget(GeolocationEvent):
    """A single AIS-tracked vessel, plotted via `geo_location_sources`.

    Our own boat (see CONF_OWN_MMSI) is represented the exact same way and
    reports the SAME `source` (GEO_LOCATION_SOURCE) as every other target, so
    it is guaranteed to show up on the map as soon as our own AIS unit's
    own-ship message is decoded — this does not depend on the GPS-based
    `device_tracker.nevera` marker being populated at all (that entity may
    still be shown separately on the map card; a second pin for the same
    boat is far less of a problem than the boat being invisible). The detail
    table (filtered by entity_id, not source) lists it with the full AIS
    attribute set like every other target, flagged via `is_own_ship`.
    """

    _attr_should_poll = False

    def __init__(
        self,
        reading: AisTargetReading,
        is_own_ship: bool,
        own_static: dict[str, Any] | None = None,
        origin: tuple[float, float] | None = None,
    ) -> None:
        self._reading = reading
        self._is_own_ship = is_own_ship
        # Point the distance is measured FROM: our own boat's live position
        # (see AisTargetsManager.own_position), not HA's static home zone.
        self._origin = origin
        # Static identity of OUR boat, straight from the config entry. Our own
        # transceiver never puts its own msg24/static data on the N2K bus
        # (verified on the live bus), so without this fallback our row would
        # stay "AIS <mmsi>" with empty name/callsign/type/size columns.
        self._own_static = own_static or {}
        # Deliberately NO unique_id: these are purely transient in-memory
        # entities (like HA core's adsb/opensky geo_location events), so they
        # never create entity_registry rows — the whole point of this
        # re-architecture is to keep HA's registry clean of passing vessels.
        # The entity_id is pinned explicitly so the map/table can reference
        # geo_location.ais_<mmsi> deterministically.
        self.entity_id = f"geo_location.ais_{reading.mmsi}"
        self._apply(reading)

    def _static(self, attr: str) -> Any:
        """Field value, falling back to the configured own-boat identity.

        The bus always wins; the configured value only fills a gap, and only
        for our own boat.
        """
        value = getattr(self._reading, attr, None)
        if value is None and self._is_own_ship:
            return self._own_static.get(attr)
        return value

    def _apply(self, reading: AisTargetReading) -> None:
        self._reading = reading
        self._attr_source = GEO_LOCATION_SOURCE
        name = self._static("vessel_name") or f"AIS {reading.mmsi}"
        self._attr_name = f"{_OWN_BOAT_ICON} {name}" if self._is_own_ship else name
        self._attr_latitude = reading.latitude
        self._attr_longitude = reading.longitude
        # A GeolocationEvent's state IS its distance, so leaving it unset
        # parks every live target at state `unknown` (which in turn made a
        # state-based auto-entities filter wipe the whole detail table).
        self._attr_distance = self._distance_km(reading)

    def _distance_km(self, reading: AisTargetReading) -> float | None:
        """Distance from OUR BOAT to this target, in km.

        The origin is our own vessel's live position, decoded off the bus (the
        GNSS receiver's PGN 129029/129025, or our own AIS target as a
        fallback). If we do not have a TRUSTWORTHY
        own position we report no distance at all: HA's configured home zone is
        deliberately NOT used as a fallback — on a moving boat it produced a
        constant, meaningless offset (a stale/broken origin once made every
        target ~1500 km away, which is what this guard prevents).
        """
        if reading.latitude is None or reading.longitude is None:
            return None
        origin = self._origin
        if origin is None:
            return None
        return round(
            distance(origin[0], origin[1], reading.latitude, reading.longitude) / 1000,
            2,
        )

    def update_from_reading(
        self,
        reading: AisTargetReading,
        is_own_ship: bool,
        own_static: dict[str, Any] | None = None,
        origin: tuple[float, float] | None = None,
    ) -> None:
        """Refresh this entity in place from a newer reading."""
        self._is_own_ship = is_own_ship
        self._own_static = own_static or {}
        self._origin = origin
        self._apply(reading)
        self.async_write_ha_state()

    def _map_label(self) -> str:
        """Marker label for the map card.

        The map card renders a geo_location marker with the entity name's
        INITIALS (3 chars max) unless the source is configured with
        `label_mode: attribute`, so the readable label lives here: vessel name
        plus its speed and length. Fields that are unavailable are simply left
        out instead of showing a dash on the map.
        """
        parts: list[str] = [str(self._attr_name)]
        sog = self._reading.sog
        if isinstance(sog, (int, float)):
            parts.append(f"{sog:.1f}kn")
        length = self._static("length")
        if isinstance(length, (int, float)):
            parts.append(f"{length:.0f}m")
        return " · ".join(parts)

    @property
    def last_seen(self) -> datetime:
        return self._reading.last_seen or dt_util.utcnow()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self._reading
        last_seen = r.last_seen or dt_util.utcnow()

        def disp(value: Any) -> Any:
            """Render missing values as an em dash.

            Plenty of AIS fields are legitimately "not available" (Class B
            reports carry no navStatus, COG/heading are often 0xFFFF, static
            data lags behind by minutes). flex-table-card renders a `null`
            attribute as an empty/`undefined` cell, which reads as a bug, so
            unavailable fields are shown as a dash instead.
            """
            return value if value is not None else "—"

        return {
            # Keep the raw numeric position out of the dash substitution: the
            # map card and any templating consume these directly.
            "mmsi": r.mmsi,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "sog": disp(r.sog),
            "cog": disp(r.cog),
            "heading": disp(r.heading),
            "nav_status": disp(r.nav_status),
            "rate_of_turn": disp(r.rate_of_turn),
            # Identity fields fall back to the configured own-boat values (for
            # our MMSI only) when the bus has not delivered static data.
            "vessel_name": disp(self._static("vessel_name")),
            "callsign": disp(self._static("callsign")),
            "ship_type": disp(self._static("ship_type")),
            "length": disp(self._static("length")),
            "beam": disp(self._static("beam")),
            "destination": disp(r.destination),
            "eta": disp(r.eta),
            "is_own_ship": self._is_own_ship,
            # Consumed by the map card via `label_mode: attribute`.
            "map_label": self._map_label(),
            # Plain LOCAL wall-clock time: the table's "Updated" column is read
            # at a glance while sailing, and a full UTC ISO timestamp is both
            # unreadable there and off by the local UTC offset. The machine
            # readable form stays available as `last_seen_iso`.
            "last_seen": dt_util.as_local(last_seen).strftime("%H:%M:%S"),
            "last_seen_iso": last_seen.isoformat(),
        }


class AisTargetsManager:
    """Owns the periodic refresh loop and the dynamic set of AisTarget entities.

    Structurally mirrors HA core's `adsb`/`opensky` geo_location platforms:
    one manager per config entry, ticking on `update_interval` and expiring
    entities whose `last_seen` exceeds `stale_timeout`. Readings come from the
    `AisBusClient` in-memory target table, not from `hass.states`.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: AisBusClient,
        async_add_entities: AddEntitiesCallback,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._client = client
        self._async_add_entities = async_add_entities
        self._entities: dict[int, AisTarget] = {}
        # Entity objects whose HA removal is still in flight. Re-adding an
        # entity_id that HA has not finished removing makes the platform
        # refuse the add ("entity id already exists"), after which the row is
        # present in the table but carries no attributes at all — which is
        # exactly the "after a while everything turns into undefined" symptom.
        self._removing: set[int] = set()
        self._refresh_lock = asyncio.Lock()
        self._unsub_interval = None

    @property
    def _options(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def update_interval(self) -> int:
        return int(
            self._options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )

    @property
    def stale_timeout(self) -> timedelta:
        minutes = int(self._options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT))
        return timedelta(minutes=minutes)

    @property
    def own_mmsi(self) -> int | None:
        raw = self._options.get(CONF_OWN_MMSI, DEFAULT_OWN_MMSI)
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None

    @property
    def own_static(self) -> dict[str, Any]:
        """Configured static identity of our own boat (blank keys dropped).

        Our transceiver broadcasts our position but not our own msg24/static
        data, so these config-entry values are the only source for the name /
        callsign / type / size of our own row.
        """
        opts = self._options

        def text(key: str) -> str | None:
            value = str(opts.get(key, "") or "").strip()
            return value or None

        def number(key: str) -> float | None:
            value = text(key)
            if value is None:
                return None
            try:
                return float(value)
            except ValueError:
                return None

        static = {
            "vessel_name": text(CONF_OWN_NAME),
            "callsign": text(CONF_OWN_CALLSIGN),
            "ship_type": text(CONF_OWN_SHIP_TYPE),
            "length": number(CONF_OWN_LENGTH),
            "beam": number(CONF_OWN_BEAM),
        }
        return {k: v for k, v in static.items() if v is not None}

    def own_position(
        self, snapshot: dict[int, AisTargetReading]
    ) -> tuple[float, float] | None:
        """Live position of OUR boat, used as the distance origin.

        Both candidates come straight off the N2K bus, read by our own gateway
        client — HA's `device_tracker.*` template sensor is deliberately NOT
        consulted: it re-derives the very same GNSS fix through the nmea2000
        integration plus a Jinja last-known-position hold, and on prod it was
        publishing a partial fix (longitude 0.0) that made every target read
        ~1500 km away.

        Priority: the GNSS receiver's own fix (PGN 129029/129025, the antennas
        out in the open sky) → our own AIS target (own_mmsi) → None (no
        distance is reported at all, see AisTarget._distance_km). Every
        candidate is validated by `_valid_origin`, because a broken origin
        silently poisons EVERY target's distance.
        """
        gnss = self._client.own_position
        if gnss is not None:
            origin = _valid_origin(gnss[0], gnss[1])
            if origin is not None:
                return origin
        own_mmsi = self.own_mmsi
        if own_mmsi is not None:
            own = snapshot.get(own_mmsi)
            if own is not None and own.has_position:
                return _valid_origin(own.latitude, own.longitude)
        return None

    def async_start(self) -> None:
        """Start the periodic refresh and run one immediately."""
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_refresh, timedelta(seconds=self.update_interval)
        )
        self.hass.async_create_task(self._async_refresh_now())

    def async_stop(self) -> None:
        """Cancel the periodic refresh (called on config entry unload)."""
        if self._unsub_interval is not None:
            self._unsub_interval()
            self._unsub_interval = None

    async def _async_refresh(self, _now: datetime) -> None:
        await self._async_refresh_now()

    async def _async_refresh_now(self) -> None:
        # Refreshes must never overlap: two concurrent passes could add and
        # remove the same entity_id at the same time.
        async with self._refresh_lock:
            await self._async_refresh_locked()

    async def _async_refresh_locked(self) -> None:
        try:
            snapshot = self._client.snapshot()
        except Exception:  # noqa: BLE001 - a refresh glitch must never crash HA
            _LOGGER.exception("ais_targets: error reading target table")
            return

        now = dt_util.utcnow()
        stale_before = now - self.stale_timeout
        own_mmsi = self.own_mmsi
        own_static = self.own_static
        origin = self.own_position(snapshot)

        seen: set[int] = set()
        for mmsi, reading in snapshot.items():
            # Only plottable, non-stale targets get an entity.
            if not reading.has_position:
                continue
            if reading.last_seen is not None and reading.last_seen < stale_before:
                continue
            seen.add(mmsi)

        # 1) Expire first, and AWAIT the removals — doing this before any add
        #    guarantees a vessel that briefly went stale and came straight
        #    back can re-register its entity_id cleanly.
        for mmsi in list(self._entities):
            entity = self._entities[mmsi]
            if mmsi in seen and entity.last_seen >= stale_before:
                continue
            del self._entities[mmsi]
            self._client.drop(mmsi)
            self._removing.add(mmsi)
            try:
                await entity.async_remove(force_remove=True)
            except Exception:  # noqa: BLE001 - removal must never break a tick
                _LOGGER.debug(
                    "ais_targets: removing %s failed", entity.entity_id, exc_info=True
                )
            finally:
                self._removing.discard(mmsi)

        # 2) Then update the survivors and add the newcomers.
        new_entities: list[AisTarget] = []
        for mmsi in seen:
            reading = snapshot[mmsi]
            is_own = own_mmsi is not None and mmsi == own_mmsi
            existing = self._entities.get(mmsi)
            if existing is not None:
                existing.update_from_reading(reading, is_own, own_static, origin)
                continue
            if self.hass.states.get(f"geo_location.ais_{mmsi}") is not None:
                # A leftover state object from a previous incarnation of this
                # target is still in the state machine; adding a second entity
                # for the same entity_id would be rejected and leave an
                # attribute-less ghost row in the table. Clear it first and
                # pick the target up on the next tick.
                self.hass.states.async_remove(f"geo_location.ais_{mmsi}")
                continue
            entity = AisTarget(reading, is_own, own_static, origin)
            self._entities[mmsi] = entity
            new_entities.append(entity)

        if new_entities:
            self._async_add_entities(new_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the geo_location platform for a config entry."""
    client: AisBusClient = hass.data[DOMAIN][entry.entry_id]["client"]
    manager = AisTargetsManager(hass, entry, client, async_add_entities)
    hass.data[DOMAIN][entry.entry_id]["manager"] = manager
    manager.async_start()
    entry.async_on_unload(manager.async_stop)
