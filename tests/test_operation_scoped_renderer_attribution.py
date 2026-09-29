import pytest
from homeassistant.core import HomeAssistant

from custom_components.home_lighting_manager.ha_observer import (
    MANAGED_SURFACE_GROUPS,
    SURFACE_MANUAL_PRECEDENCE,
    HomeAssistantShadowObserver,
)

ENTITY = "light.backyard_test_operation_scoped_leaf"
GROUP = MANAGED_SURFACE_GROUPS["backyard"]
GUARD = "input_boolean.home_lighting_ha_guard_backyard"


def make_observer(hass: HomeAssistant) -> HomeAssistantShadowObserver:
    observer = HomeAssistantShadowObserver(
        hass,
        [ENTITY],
        {ENTITY: SURFACE_MANUAL_PRECEDENCE},
    )
    observer._surface_members_by_group[GROUP] = (ENTITY,)
    observer.entity_ids = frozenset({ENTITY})
    observer.runtime.update_managed_entities(observer.entity_ids)
    return observer


@pytest.mark.asyncio
async def test_appearance_marker_does_not_cover_off(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(GUARD, "on")
    observer = make_observer(hass)

    accepted = observer.register_renderer_command_consequences(
        [ENTITY], GUARD, "appearance"
    )

    assert accepted == 1
    assert observer._active_guard_for_ha_consequence(ENTITY, "appearance") == GUARD
    assert observer._active_guard_for_ha_consequence(ENTITY, "off") is None


@pytest.mark.asyncio
async def test_off_marker_does_not_cover_appearance(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set(GUARD, "on")
    observer = make_observer(hass)

    accepted = observer.register_renderer_command_consequences(
        [ENTITY], GUARD, "off"
    )

    assert accepted == 1
    assert observer._active_guard_for_ha_consequence(ENTITY, "off") == GUARD
    assert observer._active_guard_for_ha_consequence(ENTITY, "appearance") is None
