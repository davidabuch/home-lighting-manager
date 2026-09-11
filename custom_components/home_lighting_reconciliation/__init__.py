"""Central health verification for the existing Home Lighting Manager package."""

import logging

import voluptuous as vol
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
)
from homeassistant.core import Context, callback
from homeassistant.helpers import discovery
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import CONTROL, DOMAIN, EVALUATORS, SIGNAL, SURFACES, TRANSIENTS
from .engine import GROUPS, LIQUOR, commands_in, verify
from .hue import HueEvidence
from .runtime import Reconciler

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): vol.Schema({})}, extra=vol.ALLOW_EXTRA)


class Adapter:
    def __init__(self, hass):
        self.hass = hass
        self.hue = HueEvidence(hass)
        self.runner = Reconciler(self)
        self.started = False
        self.known_members = {*GROUPS.values(), LIQUOR}
        self.last_logged = None
        self.last_evidence = ({}, {})

    def active_scripts(self):
        """Read the actually loaded baselines, including after a script reload.

        Missing/changed HA script internals fail closed rather than falling back
        to a cached copy of configuration with obsolete desired values.
        """
        component = self.hass.data.get("script")
        result = {}
        for name in [
            *("home_lighting_apply_" + s + "_baseline" for s in SURFACES),
            "home_lighting_liquor_white",
        ]:
            entity = component.get_entity("script." + name) if component else None
            if entity is None or not hasattr(entity, "script"):
                raise ValueError("Authoritative baseline not loaded")
            result[name] = {"sequence": entity.script.sequence}
        return result

    def now(self):
        return dt_util.now().isoformat()

    def publish(self, diag):
        async_dispatcher_send(self.hass, SIGNAL)
        if not diag["pending_reconciliation"] and diag["health"] == "degraded":
            signature = repr((diag.get("last_error"), diag["unresolved"]))
            if signature != self.last_logged:
                _LOGGER.warning(
                    "Home Lighting reconciliation degraded: %s; %s",
                    diag.get("last_error"),
                    diag["unresolved"],
                )
                self.last_logged = signature
        elif diag["health"] in ("healthy", "repaired"):
            self.last_logged = None

    def suppression(self):
        blocked = {}
        for entity, surfaces in {**TRANSIENTS, **EVALUATORS}.items():
            state = self.hass.states.get(entity)
            if state is None or "current" not in state.attributes:
                for surface in surfaces:
                    blocked[surface] = "missing lifecycle evidence: " + entity
            elif state.attributes["current"] > 0:
                for surface in surfaces:
                    blocked[surface] = "transient/restoration: " + entity
        # Missing ownership inputs must never be interpreted as OFF by Jinja.
        for entity in CONTROL:
            state = self.hass.states.get(entity)
            if state is None or state.state in ("unknown", "unavailable"):
                # Keep unrelated surfaces operational when a local input fails.
                surfaces = [s for s in SURFACES if s in entity]
                if "living_room" in entity or "liquor_" in entity:
                    surfaces = ["main_area"]
                elif "spa_gauge" in entity:
                    surfaces = ["backyard"]
                elif "49ers" in entity:
                    surfaces = ["main_area", "front_eve"]
                for surface in surfaces or SURFACES:
                    blocked[surface] = "missing ownership evidence: " + entity
        return blocked

    async def owners(self):
        result = await self.hass.services.async_call(
            "script", "home_lighting_resolve_owners", {}, blocking=True, return_response=True
        )
        if not result or not all(s in result for s in SURFACES):
            raise ValueError("Owner resolution failed")
        return result

    async def inspect(self):
        owners = await self.owners()
        scripts = self.active_scripts()
        blocked = self.suppression()
        active = [
            s
            for s in SURFACES
            if s not in blocked and owners[s]["owner"] not in ("sync", "manual", "spa")
        ]
        scenes = []
        for s in active:
            owner = owners[s]["owner"]
            if owner in ("holiday", "49ers"):
                scenes.append(owners[s]["scene"])
            elif owner == "daily":
                scenes.extend(
                    e
                    for service, e, _ in commands_in(
                        scripts["home_lighting_apply_" + s + "_baseline"]["sequence"]
                    )
                    if service == "scene.turn_on"
                )
        members, metadata = await self.hue.read(scenes, active) if active else ({}, {})
        self.last_evidence = members, metadata
        self.known_members.update(entity for group in members.values() for entity in group)
        # Network waits can span a new ownership event. Resolve again and never use
        # old metadata for new owners; the generation loop also invalidates this pass.
        current = await self.owners()
        if current != owners:
            blocked = {s: "ownership changed during inspection" for s in SURFACES}
        else:
            blocked = self.suppression()
        states = {
            s.entity_id: {"state": s.state, "attributes": dict(s.attributes)}
            for s in self.hass.states.async_all("light")
        }
        check = verify(current, states, scripts, members, metadata, blocked)
        snapshot = self.hass.states.get("input_boolean.home_lighting_liquor_snapshot_valid")
        if (
            "main_area" not in blocked
            and current["main_area"]["owner"] == "manual"
            and current["liquor_cabinet"]["owner"] != "door"
            and snapshot is not None
            and snapshot.state == "on"
        ):
            check.issues.append(
                {
                    "surface": "main_area",
                    "entity": LIQUOR,
                    "error": "Manual snapshot still pending release; retained without guessing",
                }
            )
        for surface, reason in blocked.items():
            if reason.startswith("missing"):
                check.issues.append({"surface": surface, "error": reason})
        return check, current

    async def repair(self, command, owners, valid):
        # Guard rearming is itself awaited; it is NOT a licence to use a stale plan.
        context = Context()
        scripts = self.active_scripts()
        check, current = await self.inspect()
        if not valid() or current != owners or command not in check.commands:
            return False
        members, metadata = self.last_evidence
        await self.hass.services.async_call(
            "script",
            "home_lighting_rearm_ha_guard",
            {"guard_entity": "input_boolean.home_lighting_ha_guard_" + command.surface},
            blocking=True,
            context=context,
        )
        # Final live checks after guard activation. No network IO here.
        current = await self.owners()
        if (
            not valid()
            or current != owners
            or command.surface in self.suppression()
            or self.active_scripts() != scripts
        ):
            return False
        states = {
            state.entity_id: {"state": state.state, "attributes": dict(state.attributes)}
            for state in self.hass.states.async_all("light")
        }
        # A device may have recovered while the guard service was awaited.
        # Recheck the discrepancy synchronously immediately before dispatch.
        final = verify(current, states, scripts, members, metadata, self.suppression())
        if command not in final.commands:
            return False
        domain, service = command.service.split(".")
        await self.hass.services.async_call(
            domain,
            service,
            {"entity_id": command.entity, **command.data},
            blocking=True,
            context=context,
        )
        return True

    @callback
    def changed(self, event):
        if not self.started:
            return
        entity = event.data["entity_id"]
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if entity in TRANSIENTS or entity in EVALUATORS:
            if old and new and old.attributes.get("current") == new.attributes.get("current"):
                return
        elif entity in CONTROL:
            if entity.endswith("_last_recall"):
                surface = entity.removeprefix("sensor.").removesuffix("_last_recall")
                guard = self.hass.states.get("input_boolean.home_lighting_ha_guard_" + surface)
                if guard and guard.state == "on":
                    return  # Our scene repair must not reset its own retry budget.
            if old and new and old.state == new.state:
                # Only score changes carry relevant attribute-based intent here.
                if entity == "sensor.nfl_san_francisco_49ers":
                    if old.attributes.get("team_score") == new.attributes.get("team_score"):
                        return
                else:
                    return
        elif entity in GROUPS.values():
            surface = next(s for s, e in GROUPS.items() if e == entity)
            guard = self.hass.states.get("input_boolean.home_lighting_ha_guard_" + surface)
            if (
                old is None
                or new is None
                or old.state == new.state
                or (guard and guard.state == "on")
            ):
                return
            # The OFF event invalidates BEFORE the existing two-second release.
        elif (
            entity in self.known_members
            and new
            and old
            and old.state in ("unknown", "unavailable")
            and new.state not in ("unknown", "unavailable")
        ):
            # A recovery triggers a bounded fresh pass, never a persistent reassert loop.
            pass
        else:
            return
        self.runner.schedule(entity)


async def async_setup(hass, config):
    adapter = Adapter(hass)
    hass.data[DOMAIN] = adapter
    await discovery.async_load_platform(hass, "sensor", DOMAIN, {}, config)
    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, adapter.changed)

    @callback
    def start(_event=None):
        adapter.started = True
        adapter.runner.schedule("homeassistant_start")

    @callback
    def stop(_event):
        adapter.started = False
        adapter.runner.close()
        unsub()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)
    if hass.is_running:
        start()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start)
    return True
