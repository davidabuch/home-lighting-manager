"""Home Lighting Manager observation-only integration boundary.

The integration currently runs only a shadow observer. It has no lighting command authority and
must not call Home Assistant services or reconciliation paths.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

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
    register_command_tracking(hass, observer)
    return True


def register_command_tracking(hass, observer):
    """Register metadata-only transactions; shared with offline adapter tests."""
    async def async_track_command_consequence(call: ServiceCall) -> None:
        identity = call.data.get("receipt_id")
        if not isinstance(identity, str) or not 0 < len(identity) <= 128:
            raise ValueError("Invalid consequence identity")
        if call.data.get("phase") == "cancel":
            observer.command_receipts.cancel(identity)
            return
        if call.data.get("phase") in ("submitted", "complete"):
            observer.command_receipts.advance(
                identity, call.data["phase"], observer.runtime.engine.generation, dt_util.now()
            )
            return
        if call.data.get("phase") != "prepare":
            raise ValueError("Invalid consequence phase")
        entity = call.data.get("entity_id")
        if entity not in observer.manual_precedence:
            raise ValueError("Consequence entity is not managed")
        observer.command_receipts.prepare(
            identity, entity, call.data.get("expected"), call.data.get("context_id"),
            observer.runtime.engine.generation, dt_util.now(),
        )

    hass.services.async_register(
        DOMAIN, "track_command_consequence", async_track_command_consequence
    )
