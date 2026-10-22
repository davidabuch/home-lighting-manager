"""Cabinet-scoped functional execution, subordinate to HLM desired ownership.

No homeowner classification lives here. A lock orders physical submissions; an
ingress epoch invalidates older metadata work before that lock can be acquired.
Transport acceptance is distinct from observed completion. No snapshots replay
after restart and no whole-scene command is issued by a functional release.
"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from homeassistant.core import Context

from custom_components.home_lighting_manager.command_receipts import signature_matches
from custom_components.home_lighting_manager.model import Appearance

from .engine import LIQUOR, LIQUOR_WHITE, commands_in
from .rendering import projection

DOORS = ("binary_sensor.liquor_cabinet_l_door", "binary_sensor.liquor_cabinet_door_r")
SYNC = "binary_sensor.hue_bridge_living_room"
WHITE = LIQUOR_WHITE


class DeferredRelease(ValueError):
    """Current evidence does not authorize a functional command."""


@dataclass
class DoorInterval:
    identity: str = field(default_factory=lambda: uuid4().hex)
    snapshot: dict | None = None
    phase: str = "open"
    white_sent: bool = False


class Cabinet:
    def __init__(self, adapter):
        self.adapter = adapter
        self.hass = adapter.hass
        self.lock = asyncio.Lock()
        self.epoch = 0
        self.interval = None
        self.pending = None
        self.last = {"status": "idle"}
        self.previous_inputs = None

    def inputs(self):
        return tuple(self.hass.states.get(e).state if self.hass.states.get(e) else None
                     for e in (*DOORS, SYNC))

    def view(self):
        view = projection(self.hass)
        if LIQUOR not in view.get("entities", {}):
            raise DeferredRelease("canonical_cabinet_ownership_unavailable")
        return view

    def signature(self):
        view = self.view()
        return (
            view["authority_id"], view["generation"], deepcopy(view["entities"][LIQUOR]),
            LIQUOR in view.get("pending_entities", ()), self.inputs(),
        )

    def note(self, status, **detail):
        self.last = {
            "status": status, "epoch": self.epoch,
            "interval_id": self.interval.identity if self.interval else None,
            **detail,
        }
        self.adapter.runner.diag["cabinet"] = self.last
        self.adapter.render_note("main_area", "cabinet:" + status, [LIQUOR])
        return dict(self.last)

    def observe(self):
        """Finish only the exact still-current accepted release, never from OFF alone."""
        if self.pending is None:
            return
        token, signature, expected = self.pending
        try:
            current = token == self.epoch and signature == self.signature()
        except ValueError:
            current = False
        state = self.hass.states.get(LIQUOR)
        if current and state and state.state in ("on", "off") and signature_matches(
            expected, {**dict(state.attributes), "on": state.state == "on"}
        ):
            self.pending = None
            self.interval = None
            self.note("released_verified")

    def invalidate_inputs(self):
        inputs = self.inputs()
        if inputs == self.previous_inputs:
            return
        self.epoch += 1
        self.pending = None
        if "on" in inputs[:2] and (
            self.previous_inputs is None or self.previous_inputs[:2] == ("off", "off")
        ):
            try:
                self.interval = DoorInterval(snapshot=deepcopy(self.view()["entities"][LIQUOR]))
            except ValueError:
                pass  # No physical-state fallback or invented Manual snapshot.
        self.previous_inputs = inputs

    async def update(self, reason="door_state", context=None):
        """Resolve current truth. Every request fences earlier suspended work."""
        self.invalidate_inputs()
        self.epoch += 1
        token = self.epoch
        self.pending = None
        inputs = self.inputs()
        opened = "on" in inputs[:2]
        try:
            view = self.view()
        except ValueError as err:
            return self.note("blocked", reason=str(err))
        if opened and self.interval is None:
            # Capture canonical intent exactly once, never the physical white.
            self.interval = DoorInterval(snapshot=deepcopy(view["entities"][LIQUOR]))
        if reason == "ownership_changed" and opened and self.interval.white_sent:
            return self.note("underlay_changed")  # Do not fight independent intent.
        if self.interval is None and reason == "ownership_changed":
            return self.note("idle")
        if self.interval is None:
            self.interval = DoorInterval(snapshot=None, phase="release")
        if inputs[2] != "off":
            return self.note("yield_sync" if inputs[2] == "on" else "blocked_sync_unknown")
        if not opened and inputs[:2] != ("off", "off"):
            return self.note("blocked_sensors_unknown")
        state = self.hass.states.get(LIQUOR)
        if state is None or state.state not in ("on", "off"):
            return self.note("blocked_light_unavailable")

        async with self.lock:
            if token != self.epoch or inputs != self.inputs():
                return self.note("stale_request")
            identity = None
            submitted = False
            try:
                view = self.view()
                signature = self.signature()
                if signature[3]:
                    raise DeferredRelease("provisional_homeowner_intent")
                self.interval.phase = "open" if opened else "release"
                owners = await self.adapter.owners()
                automatic = (owners["main_area"]["owner"], owners["main_area"].get("scene"))
                scripts = deepcopy(self.adapter.active_scripts()[
                    "home_lighting_apply_main_area_baseline"])
                if opened:
                    command, data, expected, scene = (
                        "turn_on", dict(WHITE), {"on": True, **{k: v for k, v in WHITE.items()
                                                             if k != "transition"}}, None
                    )
                else:
                    command, data, expected, scene = await self.release_plan(view, owners)

                async def valid():
                    current = await self.adapter.owners()
                    return (
                        token == self.epoch and self.signature() == signature
                        and (current["main_area"]["owner"], current["main_area"].get("scene"))
                        == automatic
                        and self.adapter.active_scripts()["home_lighting_apply_main_area_baseline"]
                        == scripts
                        and self.inputs()[2] == "off"
                        and (s := self.hass.states.get(LIQUOR)) is not None
                        and s.state in ("on", "off")
                    )

                if not await valid():
                    return self.note("stale_plan")
                dispatch_context = Context(parent_id=context.id if context else None)
                identity = uuid4().hex
                await self.track(identity, "prepare", expected, dispatch_context)
                if not await valid():
                    await self.track(identity, "cancel")
                    return self.note("stale_plan")
                await self.track(identity, "submitted")
                if not await valid():
                    await self.track(identity, "cancel")
                    return self.note("stale_plan")
                # Nothing awaited between this fence and physical submission.
                # Once accepted by Hue, a request cannot be recalled. The lock
                # orders the newer request after its predecessor's acknowledgement.
                sent = True
                submitted = True
                if scene is None:
                    await self.hass.services.async_call(
                        "light", command, {"entity_id": LIQUOR, **data},
                        blocking=True, context=dispatch_context,
                    )
                else:
                    sent = LIQUOR in await self.adapter.hue.apply_actions(
                        scene, protected=set(scene["actions"]) - {LIQUOR}, valid=valid
                    )
                if not sent:
                    await self.track(identity, "cancel")
                    return self.note("rejected_dispatch")
                await self.track(identity, "complete")
                if not await valid():
                    return self.note("superseded_after_submission")
                if opened:
                    self.interval.white_sent = True
                    return self.note("white_submitted", receipt_id=identity)
                self.pending = (token, signature, expected)
                self.note("awaiting_verification")
                self.observe()
                return dict(self.last)
            except DeferredRelease as err:
                return self.note("blocked", reason=str(err))
            except asyncio.CancelledError:
                if identity is not None and not submitted:
                    await self.track(identity, "cancel")
                self.note("cancelled")
                raise
            except Exception:
                if identity is not None and not submitted:
                    await self.track(identity, "cancel")
                self.note("failed")
                raise

    async def track(self, identity, phase, expected=None, context=None):
        await self.hass.services.async_call(
            "home_lighting_manager", "track_command_consequence",
            {"receipt_id": identity, "phase": phase, "entity_id": LIQUOR,
             **({"expected": expected, "context_id": context.id} if expected is not None else {})},
            blocking=True,
        )

    async def release_plan(self, view, owners):
        record = view["entities"][LIQUOR]
        if record["kind"] in ("manual", "manual_off"):
            desired = record.get("desired")
            if not isinstance(desired, dict):
                raise DeferredRelease("manual_desired_state_missing")
            if desired.get("scene_id"):
                # No supported API evidence currently establishes per-member
                # dynamic rejoin. Retain identity; never sample or recall peers.
                raise DeferredRelease("native_manual_scene_rejoin_requires_capability")
            return appearance_plan(desired)
        if record["protected"]:
            raise DeferredRelease("unsupported_exposed_layer")
        required = (
            "input_boolean.home_lighting_main_area_window",
            "input_boolean.home_lighting_holiday_active",
            "sensor.nfl_san_francisco_49ers",
        )
        if any((s := self.hass.states.get(entity)) is None
               or s.state in ("unknown", "unavailable") for entity in required):
            raise DeferredRelease("automatic_ownership_inputs_unavailable")
        owner = owners["main_area"]["owner"]
        if record["kind"] == "automatic" and (record.get("family") or record.get("session_id")) and record["owner"] != owner:
            raise DeferredRelease("canonical_automatic_layer_differs_from_legacy_underlay")
        if owner == "off":
            return "turn_off", {"transition": 0}, {"on": False}, None
        if owner == "daily":
            commands = commands_in(self.adapter.active_scripts()[
                "home_lighting_apply_main_area_baseline"]["sequence"])
            entries = [(s, p) for s, e, p in commands if e == LIQUOR]
            if len(entries) != 1 or entries[0][0] != "light.turn_on":
                raise DeferredRelease("cabinet_daily_presentation_unavailable")
            data = dict(entries[0][1])
            data["transition"] = 0
            return "turn_on", data, {"on": True, **{k: v for k, v in data.items()
                                                     if k != "transition"}}, None
        if owner not in ("holiday", "49ers"):
            raise DeferredRelease("unsupported_current_owner")
        scene_id = owners["main_area"].get("scene")
        if not scene_id:
            raise DeferredRelease("current_scene_unavailable")
        members, scenes = await self.adapter.hue.read([scene_id], ["main_area"])
        scene = scenes.get(scene_id, {})
        if LIQUOR not in members.get("main_area", ()) or LIQUOR not in scene.get("actions", {}):
            raise DeferredRelease("cabinet_scene_action_unavailable")
        if scene.get("auto_dynamic") is not False or scene.get("active") == "dynamic_palette":
            raise DeferredRelease("native_dynamic_scene_rejoin_requires_capability")
        expected = action_signature(scene["actions"][LIQUOR]["action"])
        return "hue_action", {}, expected, scene


def appearance_plan(desired):
    native = {k: v for k, v in desired.items() if v is not None}
    for key in ("xy_color", "rgb_color", "hs_color"):
        if key in native:
            native[key] = tuple(native[key])
    if not Appearance(**native).is_valid():
        raise DeferredRelease("invalid_manual_desired_state")
    if native["on"] is False:
        return "turn_off", {"transition": 0}, {"on": False}, None
    mode = native.get("color_mode")
    data = {k: native[k] for k in ("brightness", "effect") if k in native}
    channels = {"xy": "xy_color", "color_temp": "color_temp_kelvin",
                "rgb": "rgb_color", "hs": "hs_color"}
    if mode in channels:
        data[channels[mode]] = native[channels[mode]]
    elif mode is None:
        present = [key for key in channels.values() if key in native]
        if len(present) > 1:
            raise DeferredRelease("ambiguous_native_color_mode")
        if present:
            data[present[0]] = native[present[0]]
    data = {key: list(value) if isinstance(value, tuple) else value for key, value in data.items()}
    return "turn_on", {**data, "transition": 0}, {"on": True, **data}, None


def action_signature(action):
    """Translate protocol units only, retaining the native color model."""
    if set(action) - {"on", "dimming", "color", "color_temperature", "effects", "dynamics"}:
        raise DeferredRelease("unsupported_native_action_signature")
    expected = {"on": action["on"]["on"]}
    if not expected["on"]:
        return expected
    if "dimming" in action:
        expected["brightness"] = round(action["dimming"]["brightness"] * 255 / 100)
    if "color" in action:
        xy = action["color"]["xy"]
        expected["xy_color"] = [xy["x"], xy["y"]]
    if "color_temperature" in action:
        expected["color_temp_kelvin"] = round(1000000 / action["color_temperature"]["mirek"])
    if "effects" in action:
        value = action["effects"]["effect"]
        expected["effect"] = "off" if value == "no_effect" else value
    return expected
