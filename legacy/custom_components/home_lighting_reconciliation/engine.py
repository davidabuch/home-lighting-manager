"""Semantic verification, independent of Home Assistant and its event loop.

Ownership is supplied by the same read-only script used by the evaluators.
Hue actions/topology are fetched at check time; observed colours are never truth.
"""

from dataclasses import dataclass, field
from typing import Any

from .const import SURFACES

GROUPS = dict(
    zip(
        SURFACES,
        (
            "light.holiday_main_area",
            "light.front_eve_zone",
            "light.holiday_path",
            "light.holiday_backyard",
        ),
        strict=True,
    )
)
LIQUOR = "light.living_room_liquor_cabinet_light"


@dataclass(frozen=True)
class Command:
    surface: str
    service: str
    entity: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Check:
    issues: list[dict] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def commands_in(sequence):
    """Read static baseline commands without duplicating their values."""
    result = []
    for step in sequence:
        if "sequence" in step:
            result.extend(commands_in(step["sequence"]))
        elif "parallel" in step:
            result.extend(commands_in(step["parallel"]))
        elif "if" in step:
            # Only the existing liquor-door conditional occurs in baselines.
            result.extend(commands_in(step["then"]))
        elif step.get("action") in ("light.turn_on", "light.turn_off", "scene.turn_on"):
            entities = step["target"]["entity_id"]
            if isinstance(entities, str):
                entities = [entities]
            if not isinstance(entities, list) or not all(isinstance(e, str) for e in entities):
                raise ValueError("Baseline targets must be explicit entities")
            result.extend((step["action"], entity, step.get("data", {})) for entity in entities)
        else:
            raise ValueError("Unsupported baseline action; refuse to guess desired state")
    return result


def static_diff(state, service, data):
    expected = "on" if service == "light.turn_on" else "off"
    if state is None or state["state"] in ("unknown", "unavailable"):
        return {"availability": "missing" if state is None else state["state"]}, False
    if state["state"] != expected:
        return {"state": {"expected": expected, "actual": state["state"]}}, True
    if expected == "off":
        return {}, True
    attr = state.get("attributes", {})
    diff = {}
    # Hue rounds HA brightness and converts kelvin through integer mirek.
    for key, tolerance in [("brightness", 2), ("color_temp_kelvin", 35), ("xy_color", 0.003)]:
        if key not in data:
            continue
        actual, wanted = attr.get(key), data[key]
        try:
            match = (
                (
                    len(actual) == len(wanted)
                    and all(abs(a - b) <= tolerance for a, b in zip(actual, wanted, strict=True))
                )
                if key == "xy_color"
                else abs(actual - wanted) <= tolerance
            )
        except (TypeError, ValueError):
            match = False
        if not match:
            diff[key] = {"expected": wanted, "actual": actual}
    if "effect" in data and attr.get("effect") != data["effect"]:
        diff["effect"] = {"expected": data["effect"], "actual": attr.get("effect")}
    return diff, True


def verify(owners, states, scripts, members, scene_info, suppressed=None):
    """Produce commands only for safe, observed discrepancies.

    scene_info is normalized current Hue scene action metadata, not a state snapshot.
    A different most-recent scene is ambiguous homeowner intent: fail closed.
    """
    out = Check()
    suppressed = suppressed or {}
    door = owners["liquor_cabinet"]["owner"] == "door"
    for surface in SURFACES:
        owner = owners[surface]["owner"]
        manual_entities = (
            set(owners["main_area"].get("manual_entities", []))
            if surface == "main_area"
            else set()
        )
        if surface in suppressed or owner in ("sync", "manual", "spa"):
            out.skipped[surface] = suppressed.get(surface, owner)
            # Manual has a functional liquor exception; Sync and transients do not.
            if surface == "main_area" and owner == "manual" and door and surface not in suppressed:
                _static(
                    out,
                    surface,
                    states,
                    *commands_in(scripts["home_lighting_liquor_white"]["sequence"])[0],
                )
            continue
        if surface not in members or not members[surface]:
            out.issues.append({"surface": surface, "error": "unresolved group membership"})
            continue
        if owner == "off":
            for entity in members[surface]:
                if entity in manual_entities:
                    continue
                if entity != LIQUOR or not door:
                    _static(out, surface, states, "light.turn_off", entity, {"transition": 0})
        elif owner == "daily":
            baseline = commands_in(
                scripts["home_lighting_apply_" + surface + "_baseline"]["sequence"]
            )
            for service, entity, data in baseline:
                if entity in manual_entities:
                    continue
                if service == "scene.turn_on":
                    _scene(out, surface, entity, states, scene_info, members[surface], door)
                elif entity != LIQUOR or not door:
                    if entity not in members[surface]:
                        out.issues.append(
                            {
                                "surface": surface,
                                "entity": entity,
                                "error": "baseline outside managed surface",
                            }
                        )
                    else:
                        _static(out, surface, states, service, entity, data)
        elif owner in ("holiday", "49ers"):
            scene = owners[surface]["scene"]
            protected = manual_entities if surface == "main_area" else set()
            _scene(
                out,
                surface,
                scene,
                states,
                scene_info,
                members[surface],
                door,
                protected=protected,
            )
        else:
            out.issues.append({"surface": surface, "error": "unknown owner", "owner": owner})
        if surface == "main_area" and door:
            _static(
                out,
                surface,
                states,
                *commands_in(scripts["home_lighting_liquor_white"]["sequence"])[0],
            )
    return out


def _static(out, surface, states, service, entity, data):
    diff, available = static_diff(states.get(entity), service, data)
    if diff:
        out.issues.append({"surface": surface, "entity": entity, "difference": diff})
        if available:
            out.commands.append(Command(surface, service, entity, data))


def _scene(out, surface, scene, states, scene_info, members, door, protected=None):
    protected = set(protected or ())
    info = scene_info.get(scene)
    if not info or info.get("error"):
        out.issues.append(
            {
                "surface": surface,
                "entity": scene,
                "error": (info or {}).get("error", "missing scene metadata"),
            }
        )
        return
    expected = info["actions"]
    if not expected or not set(expected).issubset(set(members)):
        out.issues.append(
            {
                "surface": surface,
                "entity": scene,
                "error": "scene crosses surface or has no actions",
            }
        )
        return
    # Do not turn a missed external recall into an automatic takeover.
    if not info.get("latest"):
        out.issues.append(
            {
                "surface": surface,
                "entity": scene,
                "error": "different or unknown latest recall; possible Manual intent",
            }
        )
        return
    unavailable = False
    drift = False
    for entity, scene_action in expected.items():
        if entity in protected:
            continue
        if entity == LIQUOR and door:
            continue

        action = scene_action.get("action", {})
        on = action.get("on", {}).get("on")

        if not isinstance(on, bool):
            out.issues.append(
                {
                    "surface": surface,
                    "entity": entity,
                    "error": "scene action missing authoritative on/off state",
                }
            )
            continue

        diff, available = static_diff(
            states.get(entity),
            "light.turn_on" if on else "light.turn_off",
            {},
        )
        if diff:
            out.issues.append({"surface": surface, "entity": entity, "difference": diff})
            drift |= available
            unavailable |= not available
    if drift:
        # A whole-scene recall could overwrite the open-door overlay or spam an
        # unavailable member. Do not guess how to restart a single dynamic palette.
        if unavailable or (surface == "main_area" and door):
            out.issues.append(
                {
                    "surface": surface,
                    "entity": scene,
                    "error": "scene repair deferred for protected/unavailable member",
                }
            )
        else:
            if protected:
                out.commands.append(
                    Command(
                        surface,
                        "hue.apply_scene_actions",
                        scene,
                        {"protected": sorted(protected)},
                    )
                )
            else:
                out.commands.append(Command(surface, "scene.turn_on", scene))
