"""Persistent central reconciliation diagnostics."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SIGNAL


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([Health(hass.data[DOMAIN])])


class Health(RestoreEntity, SensorEntity):
    _attr_name = "Home Lighting Reconciliation Health"
    _attr_unique_id = "home_lighting_reconciliation_health"
    _attr_should_poll = False
    _attr_icon = "mdi:lightbulb-check"

    def __init__(self, adapter):
        self.adapter = adapter

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if previous := await self.async_get_last_state():
            for key in (
                "last_repair",
                "last_repair_surface",
                "last_repair_entities",
                "repair_count_today",
                "repair_count_date",
            ):
                if key in previous.attributes:
                    self.adapter.runner.diag[key] = previous.attributes[key]
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))

    @property
    def native_value(self):
        return self.adapter.runner.diag["health"]

    @property
    def extra_state_attributes(self):
        return {k: v for k, v in self.adapter.runner.diag.items() if k != "health"}
