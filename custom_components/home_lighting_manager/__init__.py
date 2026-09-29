"""Home Lighting Manager observation-only integration boundary.

The integration currently runs only a shadow observer. It has no lighting command authority and
must not call Home Assistant services or reconciliation paths.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall

from .ha_observer import (
    CONF_MANUAL_PRECEDENCE,
    CONF_SHADOW_ENTITIES,
    CONF_SURFACE_EXCLUSIONS,
    DOMAIN,
)
from .ha_observer import CONFIG_SCHEMA as CONFIG_SCHEMA
from .promotion_observer import PromotingHomeAssistantShadowObserver


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the observation-only Home Lighting Manager shadow runtime."""
    domain_config = config.get(DOMAIN)
    if not isinstance(domain_config, dict):
        return True

    entity_ids = domain_config.get(CONF_SHADOW_ENTITIES, [])
    manual_precedence = domain_config.get(CONF_MANUAL_PRECEDENCE, {})
    surface_exclusions = domain_config.get(CONF_SURFACE_EXCLUSIONS, {})
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        list(entity_ids),
        dict(manual_precedence),
        {surface: list(items) for surface, items in dict(surface_exclusions).items()},
    )
    await observer.async_start()
    hass.data[DOMAIN] = observer

    async def async_reset_ownership(_call: ServiceCall) -> None:
        await observer.async_reset_homeowner_control()

    async def async_mark_command_consequence(call: ServiceCall) -> None:
        entity_ids = call.data.get("entity_ids", ())
        guard_entity = call.data.get("guard_entity")
        operation = call.data.get("operation")
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if (
            not isinstance(entity_ids, (list, tuple))
            or not isinstance(guard_entity, str)
            or operation not in ("appearance", "off")
        ):
            raise ValueError("Invalid renderer attribution payload")
        accepted = observer.register_renderer_command_consequences(
            entity_ids, guard_entity, operation
        )
        if accepted != len(entity_ids):
            raise ValueError("Renderer attribution rejected one or more entities")

    hass.services.async_register(DOMAIN, "reset_ownership", async_reset_ownership)
    hass.services.async_register(
        DOMAIN, "mark_command_consequence", async_mark_command_consequence
    )
    return True
