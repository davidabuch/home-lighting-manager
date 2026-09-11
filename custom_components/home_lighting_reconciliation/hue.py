"""Read-only Hue topology/actions through the existing HA Hue entry.

No credentials or observed light colours are copied into configuration/diagnostics.
"""

import aiohttp
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .engine import GROUPS


class HueEvidence:
    def __init__(self, hass):
        self.hass = hass

    async def read(self, scenes, surfaces):
        registry = er.async_get(self.hass)
        targets = {
            entity: registry.async_get(entity)
            for entity in [*(GROUPS[s] for s in surfaces), *scenes]
        }
        entries = {entry.config_entry_id for entry in targets.values() if entry}
        resources = {}
        for entry_id in entries:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if not entry or entry.domain != "hue":
                continue
            try:
                async with async_get_clientsession(self.hass, verify_ssl=False).get(
                    f"https://{entry.data['host']}/clip/v2/resource",
                    headers={"hue-application-key": entry.data["api_key"]},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                    if payload.get("errors"):
                        raise ValueError("Hue rejected resource request")
                    resources[entry_id] = {r["id"]: r for r in payload["data"]}
            except Exception:
                # Avoid logging request headers or exception text containing URLs.
                resources[entry_id] = {}
        members = {}
        info = {}
        for surface in surfaces:
            entry = targets[GROUPS[surface]]
            if not entry:
                continue
            resource = resources.get(entry.config_entry_id, {})
            group_light = resource.get(entry.unique_id, {})
            group = resource.get(group_light.get("owner", {}).get("rid"), {})
            lights = set()
            valid = bool(group.get("children"))
            for child in group.get("children", []):
                if child["rtype"] == "light":
                    lights.add(child["rid"])
                elif child["rtype"] == "device":
                    services = resource.get(child["rid"], {}).get("services", [])
                    lights.update(s["rid"] for s in services if s["rtype"] == "light")
                    valid &= bool(services)
                else:
                    valid = False
            entities = [registry.async_get_entity_id("light", "hue", rid) for rid in lights]
            if valid and entities and all(entities):
                members[surface] = sorted(entities)
        for scene_id in scenes:
            entry = targets[scene_id]
            if not entry:
                continue
            resource = resources.get(entry.config_entry_id, {})
            scene = resource.get(entry.unique_id, {})
            actions = {}
            valid = bool(scene.get("actions"))
            for action in scene.get("actions", []):
                target = action.get("target", {})
                entity = registry.async_get_entity_id("light", "hue", target.get("rid", ""))
                on = action.get("action", {}).get("on", {}).get("on")
                if target.get("rtype") != "light" or not entity or not isinstance(on, bool):
                    valid = False
                else:
                    actions[entity] = on
            # Equivalent room/zone recalls are evidence of possible homeowner
            # intent even while the separate monitor's coverage fix awaits review.
            wanted_members = _group_members(resource, scene.get("group", {}).get("rid"))
            equivalent = {
                rid
                for rid, obj in resource.items()
                if obj.get("type") in ("room", "zone")
                and wanted_members
                and _group_members(resource, rid) == wanted_members
            }
            recalls = [
                r
                for r in resource.values()
                if r.get("type") == "scene"
                and r.get("group", {}).get("rid") in equivalent
                and r.get("status", {}).get("last_recall")
            ]
            latest = max(recalls, key=lambda r: r["status"]["last_recall"], default={})
            if valid:
                info[scene_id] = {"actions": actions, "latest": latest.get("id") == entry.unique_id}
            else:
                info[scene_id] = {"error": "unresolved Hue scene actions"}
        return members, info


def _group_members(resources, group_id):
    """Resolve device children to lights; ignore sensors with no light service."""
    group = resources.get(group_id, {})
    if not group.get("children"):
        return None
    result = set()
    for child in group["children"]:
        if child["rtype"] == "light":
            result.add(child["rid"])
        elif child["rtype"] == "device" and child["rid"] in resources:
            result.update(
                s["rid"]
                for s in resources[child["rid"]].get("services", [])
                if s["rtype"] == "light"
            )
        else:
            return None
    return result
