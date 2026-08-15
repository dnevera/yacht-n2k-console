"""The ais_targets integration.

Reads the raw NMEA 2000 stream directly from the YDNU-02 TCP gateway, decodes
AIS PGNs in-process (see ais_bus.py), and exposes each tracked vessel as a
dynamic `geo_location.ais_<mmsi>` entity that Home Assistant's stock `map` card
plots via `geo_location_sources: ['ais_targets']`.

Crucially this never involves the `nmea2000` HA integration for AIS, so HA's
device/entity registry is never polluted with a throwaway device per passing
MMSI (the whole reason for this re-architecture).
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .ais_bus import AisBusClient
from .const import (
    CONF_GW_HOST,
    CONF_GW_PORT,
    DEFAULT_GW_HOST,
    DEFAULT_GW_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["geo_location"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up ais_targets (YAML configuration is not supported)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ais_targets from a config entry."""
    options = {**entry.data, **entry.options}
    host = str(options.get(CONF_GW_HOST, DEFAULT_GW_HOST))
    port = int(options.get(CONF_GW_PORT, DEFAULT_GW_PORT))

    client = AisBusClient(host, port)
    client.start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"client": client}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry: stop the platform and the gateway reader."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data and (client := data.get("client")) is not None:
        await client.stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (host/port/mmsi/intervals)."""
    await hass.config_entries.async_reload(entry.entry_id)
