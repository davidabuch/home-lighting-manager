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

    async def apply_actions(self, scene_info, protected=()):
        """Apply authoritative Hue V2 light actions except protected HA entities.

        The complete plan is validated before any network write so malformed or
        cross-bridge evidence cannot leave a scene partially applied.
        """
        registry = er.async_get(self.hass)
        protected = set(protected)
        actions = scene_info.get("actions") if isinstance(scene_info, dict) else None
        if not isinstance(actions, dict) or not actions:
            raise ValueError("Authoritative Hue scene actions unavailable")

        plan = []
        entry_ids = set()

        for entity, scene_action in actions.items():
            if entity in protected:
                continue
            if not isinstance(scene_action, dict):
                raise ValueError("Invalid Hue scene action evidence")

            rid = scene_action.get("rid")
            action = scene_action.get("action")
            entry = registry.async_get(entity)

            if (
                not isinstance(rid, str)
                or not rid
                or not isinstance(action, dict)
                or not action
                or entry is None
                or entry.platform != "hue"
                or entry.unique_id != rid
                or not entry.config_entry_id
            ):
                raise ValueError("Invalid Hue scene action evidence")

            plan.append((entity, rid, action))
            entry_ids.add(entry.config_entry_id)

        if not plan:
            return []

        if len(entry_ids) != 1:
            raise ValueError("Hue scene actions span multiple bridges")

        entry = self.hass.config_entries.async_get_entry(entry_ids.pop())
        if (
            not entry
            or entry.domain != "hue"
            or not entry.data.get("host")
            or not entry.data.get("api_key")
        ):
            raise ValueError("Authoritative Hue bridge unavailable")

        session = async_get_clientsession(self.hass, verify_ssl=False)
        applied = []

        for entity, rid, action in plan:
            try:
                async with session.put(
                    f"https://{entry.data['host']}/clip/v2/resource/light/{rid}",
                    headers={"hue-application-key": entry.data["api_key"]},
                    json=action,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                    if payload.get("errors"):
                        raise ValueError("Hue rejected light action")
            except Exception as err:
                # Never propagate URL/header text from the HTTP implementation.
                if isinstance(err, ValueError) and str(err) == "Hue rejected light action":
                    raise
                raise ValueError("Hue light action failed") from None

            applied.append(entity)

        return applied

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
            for scene_action in scene.get("actions", []):
                target = scene_action.get("target", {})
                rid = target.get("rid", "")
                entity = registry.async_get_entity_id("light", "hue", rid)
                action = scene_action.get("action", {})
                on = action.get("on", {}).get("on")

                if (
                    target.get("rtype") != "light"
                    or not entity
                    or not rid
                    or not isinstance(on, bool)
                    or not isinstance(action, dict)
                ):
                    valid = False
                else:
                    actions[entity] = {
                        "rid": rid,
                        "action": action,
                    }
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
                info[scene_id] = {
                    "actions": actions,
                    "palette": scene.get("palette"),
                    "speed": scene.get("speed"),
                    "latest": latest.get("id") == entry.unique_id,
                }
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
