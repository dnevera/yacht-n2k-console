"""Config flow for the ais_targets integration.

Exposes the whole configuration surface (single source of truth): the gateway
endpoint the component reads AIS from, our own vessel's MMSI, and the refresh /
stale-timeout tuning. A single-step user flow plus an equivalent options flow
(for changing values later without reinstalling) covers everything.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_GW_HOST,
    CONF_GW_PORT,
    CONF_OWN_BEAM,
    CONF_OWN_CALLSIGN,
    CONF_OWN_LENGTH,
    CONF_OWN_MMSI,
    CONF_OWN_NAME,
    CONF_OWN_SHIP_TYPE,
    CONF_STALE_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_GW_HOST,
    DEFAULT_GW_PORT,
    DEFAULT_OWN_BEAM,
    DEFAULT_OWN_CALLSIGN,
    DEFAULT_OWN_LENGTH,
    DEFAULT_OWN_MMSI,
    DEFAULT_OWN_NAME,
    DEFAULT_OWN_SHIP_TYPE,
    DEFAULT_STALE_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_GW_HOST,
                default=defaults.get(CONF_GW_HOST, DEFAULT_GW_HOST),
            ): str,
            vol.Required(
                CONF_GW_PORT,
                default=defaults.get(CONF_GW_PORT, DEFAULT_GW_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            # Left blank by default. Our own AIS unit broadcasts its own MMSI
            # onto the bus too (own-ship message); filling this in flags that
            # target as our boat ("Bumblebee") with a distinct geo_location
            # source so it is never plotted twice next to device_tracker.nevera.
            vol.Optional(
                CONF_OWN_MMSI,
                default=defaults.get(CONF_OWN_MMSI, DEFAULT_OWN_MMSI),
            ): str,
            # Own-boat static identity. Our transceiver puts our position on
            # the bus but not our own msg24/static data, so without these the
            # own row shows as "AIS <mmsi>" with empty identity columns. Used
            # ONLY as a fallback for the `own_mmsi` target.
            vol.Optional(
                CONF_OWN_NAME,
                default=defaults.get(CONF_OWN_NAME, DEFAULT_OWN_NAME),
            ): str,
            vol.Optional(
                CONF_OWN_CALLSIGN,
                default=defaults.get(CONF_OWN_CALLSIGN, DEFAULT_OWN_CALLSIGN),
            ): str,
            vol.Optional(
                CONF_OWN_SHIP_TYPE,
                default=defaults.get(CONF_OWN_SHIP_TYPE, DEFAULT_OWN_SHIP_TYPE),
            ): str,
            vol.Optional(
                CONF_OWN_LENGTH,
                default=defaults.get(CONF_OWN_LENGTH, DEFAULT_OWN_LENGTH),
            ): str,
            vol.Optional(
                CONF_OWN_BEAM,
                default=defaults.get(CONF_OWN_BEAM, DEFAULT_OWN_BEAM),
            ): str,
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
            vol.Optional(
                CONF_STALE_TIMEOUT,
                default=defaults.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
        }
    )


class AisTargetsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ais_targets."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Single step: gateway host/port + own MMSI + refresh/stale timers."""
        if self._async_current_entries():
            # One set of AIS targets for the whole boat/bus is enough.
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="AIS Targets", data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "AisTargetsOptionsFlow":
        """Expose the same options for later tuning without reinstalling."""
        return AisTargetsOptionsFlow(config_entry)


class AisTargetsOptionsFlow(config_entries.OptionsFlow):
    """Options flow: change gateway/own-MMSI/timers after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._entry.data, **self._entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
