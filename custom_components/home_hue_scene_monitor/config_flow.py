"""Config flow for Home Hue Scene Monitor."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_API_VERSION

from .const import DOMAIN


class HomeHueSceneMonitorConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Configure Home Hue Scene Monitor."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ):
        """Handle user setup."""

        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        hue_entries = self.hass.config_entries.async_entries("hue")

        if not any(
            entry.data.get(CONF_API_VERSION) == 2
            for entry in hue_entries
        ):
            return self.async_abort(reason="hue_v2_not_found")

        return self.async_create_entry(
            title="Home Hue Scene Monitor",
            data={},
        )
