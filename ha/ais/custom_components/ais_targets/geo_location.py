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
    CONF_OWN_MMSI,
    CONF_STALE_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_OWN_MMSI,
    DEFAULT_STALE_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    GEO_LOCATION_SOURCE,
)

_LOGGER = logging.getLogger(__name__)

_OWN_BOAT_NAME = "⛵ Bumblebee (Own Boat)"


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

    def __init__(self, reading: AisTargetReading, is_own_ship: bool) -> None:
        self._reading = reading
        self._is_own_ship = is_own_ship
        # Deliberately NO unique_id: these are purely transient in-memory
        # entities (like HA core's adsb/opensky geo_location events), so they
        # never create entity_registry rows — the whole point of this
        # re-architecture is to keep HA's registry clean of passing vessels.
        # The entity_id is pinned explicitly so the map/table can reference
        # geo_location.ais_<mmsi> deterministically.
        self.entity_id = f"geo_location.ais_{reading.mmsi}"
        self._apply(reading)

    def _apply(self, reading: AisTargetReading) -> None:
        self._reading = reading
        self._attr_source = GEO_LOCATION_SOURCE
        if self._is_own_ship:
            self._attr_name = reading.vessel_name or _OWN_BOAT_NAME
        else:
            self._attr_name = reading.vessel_name or f"AIS {reading.mmsi}"
        self._attr_latitude = reading.latitude
        self._attr_longitude = reading.longitude
        # A GeolocationEvent's state IS its distance, so leaving it unset
        # parks every live target at state `unknown` (which in turn made a
        # state-based auto-entities filter wipe the whole detail table).
        self._attr_distance = self._distance_km(reading)

    def _distance_km(self, reading: AisTargetReading) -> float | None:
        hass = getattr(self, "hass", None)
        if hass is None or reading.latitude is None or reading.longitude is None:
            return None
        home_lat = hass.config.latitude
        home_lon = hass.config.longitude
        if home_lat is None or home_lon is None:
            return None
        return round(
            distance(home_lat, home_lon, reading.latitude, reading.longitude) / 1000,
            2,
        )

    def update_from_reading(
        self, reading: AisTargetReading, is_own_ship: bool
    ) -> None:
        """Refresh this entity in place from a newer reading."""
        self._is_own_ship = is_own_ship
        self._apply(reading)
        self.async_write_ha_state()

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
            "vessel_name": disp(r.vessel_name),
            "callsign": disp(r.callsign),
            "ship_type": disp(r.ship_type),
            "length": disp(r.length),
            "beam": disp(r.beam),
            "destination": disp(r.destination),
            "eta": disp(r.eta),
            "is_own_ship": self._is_own_ship,
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
                existing.update_from_reading(reading, is_own)
                continue
            if self.hass.states.get(f"geo_location.ais_{mmsi}") is not None:
                # A leftover state object from a previous incarnation of this
                # target is still in the state machine; adding a second entity
                # for the same entity_id would be rejected and leave an
                # attribute-less ghost row in the table. Clear it first and
                # pick the target up on the next tick.
                self.hass.states.async_remove(f"geo_location.ais_{mmsi}")
                continue
            entity = AisTarget(reading, is_own)
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
