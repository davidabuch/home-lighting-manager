"""Home Hue Scene Monitor integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import PLATFORMS
from .coordinator import HomeHueSceneCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Home Hue Scene Monitor."""

    hue_entries = hass.config_entries.async_entries("hue")

    hue_entry = next(
        (
            candidate
            for candidate in hue_entries
            if candidate.data.get(CONF_API_VERSION) == 2
            and candidate.state.recoverable
        ),
        None,
    )

    if hue_entry is None:
        hue_entry = next(
            (
                candidate
                for candidate in hue_entries
                if candidate.data.get(CONF_API_VERSION) == 2
            ),
            None,
        )

    if hue_entry is None:
        return False

    coordinator = HomeHueSceneCoordinator(hass, hue_entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload integration."""
    return await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )
