"""The ais_targets integration.

Bridges the raw per-field `nmea2000` entities produced for AIS PGNs
(129038/129039/129040 position reports, 129794 static & voyage data) into
dynamic `geo_location.ais_<mmsi>` entities that Home Assistant's stock
`map` card can plot via `geo_location_sources: ['ais_targets']`.

See README.md in this directory for the entity_id/attribute shape
assumptions made by `geo_location.py`, which could not be verified against
a live HA instance in this environment and must be re-checked during
deployment.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Config-flow only integration (single instance, two numeric options) —
# there is no YAML schema to validate here.
PLATFORMS = ["geo_location"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up ais_targets (YAML configuration is not supported)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ais_targets from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and stop the scan loop of its platform(s)."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (scan interval / stale timeout)."""
    await hass.config_entries.async_reload(entry.entry_id)
