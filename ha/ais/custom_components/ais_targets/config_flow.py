"""Config flow for the ais_targets integration.

Only two numeric options are exposed — no credentials, no host/port — so a
single-step user flow plus an equivalent options flow (for changing the
values later without reinstalling) covers the whole surface.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_STALE_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
)


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
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
        """Single step: update interval (seconds) + stale timeout (minutes)."""
        if self._async_current_entries():
            # One set of AIS targets for the whole boat/bus is enough.
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="AIS Targets", data=user_input)

        return self.async_show_form(step_id="user", data_schema=_options_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "AisTargetsOptionsFlow":
        """Expose the same two options for later tuning without reinstalling."""
        return AisTargetsOptionsFlow(config_entry)


class AisTargetsOptionsFlow(config_entries.OptionsFlow):
    """Options flow: change scan interval / stale timeout after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(current)
        )
