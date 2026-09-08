"""Offline HA Script execution; services model devices, never contact a bridge."""

import copy
import sys
from pathlib import Path

import pytest_asyncio
import yaml
from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA
from homeassistant.core import Context, HomeAssistant, SupportsResponse, callback
from homeassistant.helpers.script import Script
from homeassistant.helpers.script_variables import ScriptVariables

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PACKAGE = yaml.safe_load((ROOT / "packages/home_lighting_manager.yaml").read_text())


class Rig:
    def __init__(self, hass):
        self.hass = hass
        self.calls = []
        self.scripts = {}
        self.snapshots = {}
        self.before_service = None
        self.fail_light = False

    def set(self, entity, state, attrs=None):
        self.hass.states.async_set(entity, state, attrs or {})

    def get(self, entity):
        return self.hass.states.get(entity)

    async def service(self, call):
        from types import SimpleNamespace

        data = dict(call.data)
        if isinstance(data.get("entity_id"), list) and len(data["entity_id"]) == 1:
            data["entity_id"] = data["entity_id"][0]
        call = SimpleNamespace(domain=call.domain, service=call.service, data=data)
        if self.before_service:
            await self.before_service(call)
        self.calls.append((call.domain + "." + call.service, dict(call.data)))
        if call.domain == "scene" and call.service == "create":
            self.snapshots["scene." + call.data["scene_id"]] = [
                self.get(e) for e in call.data["snapshot_entities"]
            ]
            return
        entities = call.data.get("entity_id", [])
        if isinstance(entities, str):
            entities = [entities]
        for entity in entities:
            old = self.get(entity)
            attrs = dict(old.attributes) if old else {}
            if call.domain.startswith("input_"):
                self.set(
                    entity,
                    call.data.get("value", "on" if call.service == "turn_on" else "off"),
                    attrs,
                )
            elif call.domain == "light":
                if self.fail_light:
                    raise RuntimeError("simulated device failure")
                attrs.update(
                    {k: v for k, v in call.data.items() if k not in ("entity_id", "transition")}
                )
                if "xy_color" in call.data:
                    attrs["color_mode"] = "xy"
                if "color_temp_kelvin" in call.data:
                    attrs["color_mode"] = "color_temp"
                self.set(entity, "on" if call.service == "turn_on" else "off", attrs)
            elif call.domain == "scene" and entity in self.snapshots:
                for state in self.snapshots[entity]:
                    if state:
                        self.set(state.entity_id, state.state, dict(state.attributes))

    def install_script(self, name, obj, domain="script", fast=False):
        raw = copy.deepcopy(obj)
        seq = raw["sequence"]
        if fast:

            def shorten(x):
                if isinstance(x, list):
                    for v in x:
                        shorten(v)
                elif isinstance(x, dict):
                    if "delay" in x:
                        x["delay"] = {"milliseconds": 1}
                    for v in x.values():
                        shorten(v)

            shorten(seq)
        raw["sequence"] = seq
        cfg = SCRIPT_ENTITY_SCHEMA(raw)
        seq = cfg["sequence"]
        entity = domain + "." + name

        @callback
        def changed():
            self.set(entity, "on" if script.is_running else "off", {"current": script.runs})

        script = Script(
            self.hass,
            seq,
            name,
            domain,
            variables=cfg.get("variables", ScriptVariables({})),
            script_mode=cfg.get("mode", "single"),
            max_runs=cfg.get("max", 10),
            change_listener=changed,
        )
        self.scripts[name] = script
        self.set(entity, "off", {"current": 0})

        async def run(call):
            result = await script.async_run(dict(call.data), call.context)
            return result.service_response if result else None

        self.hass.services.async_register(
            domain, name, run, supports_response=SupportsResponse.OPTIONAL
        )
        return script

    async def run(self, name, variables=None):
        return await self.scripts[name].async_run(variables or {}, Context())

    def lights(self):
        return [c for c in self.calls if c[0].startswith(("light.", "scene.turn_on"))]


@pytest_asyncio.fixture
async def rig(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config.time_zone = "America/Los_Angeles"
    rig = Rig(hass)
    from types import SimpleNamespace

    hass.data["script"] = SimpleNamespace(
        get_entity=lambda entity: (
            SimpleNamespace(script=rig.scripts[entity.removeprefix("script.")])
            if entity.removeprefix("script.") in rig.scripts
            else None
        )
    )
    for domain, services in {
        "light": ["turn_on", "turn_off"],
        "scene": ["turn_on", "create"],
        "input_boolean": ["turn_on", "turn_off"],
        "input_text": ["set_value"],
        "input_number": ["set_value"],
        "automation": ["trigger", "turn_on", "turn_off"],
    }.items():
        for service in services:
            hass.services.async_register(domain, service, rig.service)
    for domain in ("input_boolean", "input_text", "input_number"):
        for name in PACKAGE.get(domain, {}):
            rig.set(
                domain + "." + name,
                "off" if domain == "input_boolean" else "" if domain == "input_text" else "0",
            )
    for entity in [
        "binary_sensor.hue_bridge_living_room",
        "binary_sensor.hue_bridge_backyard",
        "binary_sensor.liquor_cabinet_door_r",
        "binary_sensor.liquor_cabinet_l_door",
        "input_boolean.spa_gauge_active",
        "input_boolean.spa_gauge_blinked",
    ]:
        rig.set(entity, "off")
    rig.set("sensor.nfl_san_francisco_49ers", "PRE")
    for name, obj in PACKAGE["script"].items():
        rig.install_script(name, obj)
    yield rig
    for script in rig.scripts.values():
        await script.async_stop()
    await hass.async_block_till_done()
