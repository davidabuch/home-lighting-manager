"""Home Lighting Manager observation-only integration boundary.

The integration currently runs only a shadow observer. It has no lighting command authority and
must not call Home Assistant services or reconciliation paths.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall

from .ha_observer import CONF_MANUAL_PRECEDENCE, CONF_SHADOW_ENTITIES, DOMAIN
from .ha_observer import CONFIG_SCHEMA as CONFIG_SCHEMA
from .promotion_observer import PromotingHomeAssistantShadowObserver


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the observation-only Home Lighting Manager shadow runtime."""
    domain_config = config.get(DOMAIN)
    if not isinstance(domain_config, dict):
        return True

    entity_ids = domain_config.get(CONF_SHADOW_ENTITIES, [])
    manual_precedence = domain_config.get(CONF_MANUAL_PRECEDENCE, {})
    observer = PromotingHomeAssistantShadowObserver(
        hass,
        list(entity_ids),
        dict(manual_precedence),
    )
    await observer.async_start()
    hass.data[DOMAIN] = observer

    async def async_reset_ownership(_call: ServiceCall) -> None:
        await observer.async_reset_homeowner_control()

    hass.services.async_register(DOMAIN, "reset_ownership", async_reset_ownership)
    return True
