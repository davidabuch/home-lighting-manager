"""Coordinator for Home Hue Scene Monitor."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN, MANAGED_ZONES, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class HomeHueSceneCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll raw Hue V2 scene recall metadata."""

    def __init__(
        self,
        hass: HomeAssistant,
        hue_entry,
    ) -> None:
        """Initialize coordinator."""
        self._host = hue_entry.data[CONF_HOST]
        self._api_key = hue_entry.data[CONF_API_KEY]
        self._session = async_get_clientsession(hass, verify_ssl=False)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch all Hue scenes and calculate latest recall per managed zone."""
        url = f"https://{self._host}/clip/v2/resource/scene"

        try:
            async with self._session.get(
                url,
                headers={"hue-application-key": self._api_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Unable to read Hue V2 scenes: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected Hue scene error: {err}") from err

        scenes = payload.get("data", [])

        result: dict[str, dict[str, Any]] = {}

        for zone_key, zone in MANAGED_ZONES.items():
            group_id = zone["group_id"]
            group_ids = (group_id, *zone.get("additional_group_ids", ()))

            matching = [
                scene
                for scene in scenes
                if (scene.get("group") or {}).get("rid") in group_ids
            ]

            recalled = [
                scene
                for scene in matching
                if (scene.get("status") or {}).get("last_recall")
            ]

            if not recalled:
                result[zone_key] = {
                    "scene_name": None,
                    "scene_id": None,
                    "last_recall": None,
                    "active": None,
                    "hue_group_id": group_id,
                    "monitored_hue_group_ids": list(group_ids),
                    "recalled_hue_group_id": None,
                    "scene_count": len(matching),
                }
                continue

            latest = max(
                recalled,
                key=lambda scene: (scene.get("status") or {}).get(
                    "last_recall", ""
                ),
            )

            metadata = latest.get("metadata") or {}
            status = latest.get("status") or {}

            result[zone_key] = {
                "scene_name": metadata.get("name"),
                "scene_id": latest.get("id"),
                "last_recall": status.get("last_recall"),
                "active": status.get("active"),
                "hue_group_id": group_id,
                "monitored_hue_group_ids": list(group_ids),
                "recalled_hue_group_id": (latest.get("group") or {}).get("rid"),
                "scene_count": len(matching),
            }

        return result
