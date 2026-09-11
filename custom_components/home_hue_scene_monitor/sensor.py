"""Sensors for Home Hue Scene Monitor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import MANAGED_ZONES
from .coordinator import HomeHueSceneCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up scene-recall sensors."""

    coordinator: HomeHueSceneCoordinator = entry.runtime_data

    async_add_entities(
        HomeHueSceneRecallSensor(
            coordinator,
            zone_key,
            zone["name"],
        )
        for zone_key, zone in MANAGED_ZONES.items()
    )


class HomeHueSceneRecallSensor(
    CoordinatorEntity[HomeHueSceneCoordinator],
    SensorEntity,
):
    """Latest Hue raw scene recall for one managed zone."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HomeHueSceneCoordinator,
        zone_key: str,
        zone_name: str,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)

        self._zone_key = zone_key

        self._attr_name = f"{zone_name} Last Recall"
        self._attr_unique_id = (
            f"home_hue_scene_{zone_key}_last_recall"
        )

    @property
    def native_value(self) -> datetime | None:
        """Return latest raw Hue recall timestamp."""
        value = self.coordinator.data[self._zone_key].get(
            "last_recall"
        )

        if not value:
            return None

        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose raw Hue scene metadata."""
        data = self.coordinator.data[self._zone_key]

        return {
            "scene_name": data.get("scene_name"),
            "scene_id": data.get("scene_id"),
            "last_recall": data.get("last_recall"),
            "active": data.get("active"),
            "hue_group_id": data.get("hue_group_id"),
            "monitored_hue_group_ids": data.get("monitored_hue_group_ids"),
            "recalled_hue_group_id": data.get("recalled_hue_group_id"),
            "scene_count": data.get("scene_count"),
        }
