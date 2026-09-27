"""Legacy physical execution of HLM's read-only effective ownership contract.

No intent classification or ownership mutation belongs here. Awaited work is invalidated
when effective ownership semantics change; diagnostic-only revision churn is ignored.
Mixed scenes use native light actions,
not a whole scene followed by restoration of protected members.
"""

from copy import deepcopy

from .const import GROUP_ALIASES, HLM_DIAGNOSTIC
from .engine import LIQUOR, commands_in


def projection(hass):
    state = hass.states.get(HLM_DIAGNOSTIC)
    if (
        state is None
        or state.state != "observing"
        or state.attributes.get("command_authority") is not False
    ):
        raise ValueError("HLM projection unavailable")
    value = state.attributes.get("effective_ownership")
    if value is None:
        return {}  # Older shadow versions expose protection only; never infer OFF.
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("entities"), dict)
    ):
        raise ValueError("Invalid HLM effective ownership projection")
    if (
        not isinstance(value.get("authority_id"), str)
        or not value["authority_id"]
        or any(type(value.get(k)) is not int for k in ("generation", "revision"))
        or len(value["entities"]) > 256
    ):
        raise ValueError("Invalid HLM projection identity")
    for record in value["entities"].values():
        if not isinstance(record, dict) or type(record.get("protected")) is not bool:
            raise ValueError("Invalid HLM entity projection")
        if record.get("kind") == "manual_off" and (
            not record["protected"]
            or not isinstance(record.get("desired"), dict)
            or record["desired"].get("on") is not False
        ):
            raise ValueError("Invalid HLM Manual-OFF projection")
    return deepcopy(value)


def same_effective_ownership(current, initial):
    """Compare executable ownership semantics, not diagnostic revision churn."""
    if not current or not initial:
        return current == initial
    return (
        current.get("authority_id") == initial.get("authority_id")
        and current.get("generation") == initial.get("generation")
        and current.get("automatic_authority") == initial.get("automatic_authority")
        and current.get("entities") == initial.get("entities")
        and current.get("groups") == initial.get("groups")
    )


INVALIDATING_MUTATION_REASONS = {
    "ownership_reset",
    "evening_activation",
    "managed_entities_changed",
    "configuration_generation_changed",
    "group_off_sequence_reset",
    "intervening_group_member_intent",
    "family_ended",
    "released_to_hlm",
    "created_group_manual_off",
}


def projection_current(hass, initial):
    """Keep plans only across non-semantic diagnostic revision churn."""
    current = projection(hass)
    if not same_effective_ownership(current, initial):
        return False
    if current.get("revision") == initial.get("revision"):
        return True
    state = hass.states.get(HLM_DIAGNOSTIC)
    reason = state.attributes.get("last_mutation_reason") if state else None
    if isinstance(reason, str) and (
        reason in INVALIDATING_MUTATION_REASONS or reason.startswith("boundary:")
    ):
        return False
    return True


def same_resolved_owners(current, initial):
    """Compare resolver policy while ignoring its diagnostic HLM projection echo."""
    if not isinstance(current, dict) or not isinstance(initial, dict):
        return current == initial
    return (
        {k: v for k, v in current.items() if k != "hlm_effective_ownership"}
        == {k: v for k, v in initial.items() if k != "hlm_effective_ownership"}
    )


def project_owners(hass, owners, members):
    """Keep automatic eligibility separate from exposed per-member desired state."""
    view = projection(hass)
    diagnostic = hass.states.get(HLM_DIAGNOSTIC)
    old_protected = (
        set(diagnostic.attributes.get("reconciliation_protected_entities") or ())
        if diagnostic
        else set()
    )
    result = deepcopy(owners)
    for surface, group in members.items():
        if surface not in result:
            continue
        records = {e: view.get("entities", {})[e] for e in group if e in view.get("entities", {})}
        protected = {e for e, v in records.items() if v["protected"]}
        protected |= {
            e
            for e, v in records.items()
            if v["kind"] == "automatic" and v["owner"] and v["owner"] != result[surface]["owner"]
        }
        existing = set(result[surface].get("manual_entities", ()))
        if view:
            existing -= set(view["entities"])
        else:
            protected |= old_protected & set(group)
        result[surface]["manual_entities"] = sorted(existing | protected)
        result[surface]["manual_off_entities"] = sorted(
            e for e, v in records.items() if v["kind"] == "manual_off"
        )
        result[surface]["excluded_entities"] = (
            sorted(set(group) - set(view["entities"])) if view else []
        )
        result[surface]["effective_entities"] = records
        result[surface]["ownership_token"] = {
            k: view.get(k) for k in ("authority_id", "generation", "revision")
        }
    return result


async def render(adapter, surface, service, entities, parameters, context, expected_owner=None):
    """Filter one legacy renderer request immediately before physical dispatch."""
    hass = adapter.hass
    if set(parameters) & {"entity_id", "area_id", "device_id", "floor_id", "label_id"}:
        raise ValueError("Renderer parameters cannot override target scope")
    if isinstance(entities, str):
        entities = [entities]
    if not entities:
        return
    initial = projection(hass)
    owners = await adapter.owners()
    if not projection_current(hass, initial):
        return
    if expected_owner is not None and owners[surface]["owner"] != expected_owner:
        adapter.render_note(surface, "obsolete_automatic_owner", entities)
        return
    if owners[surface]["owner"] in ("sync", "manual") or (
        owners[surface]["owner"] == "spa" and expected_owner != "spa"
    ):
        adapter.render_note(surface, "structural_or_legacy_owner", entities)
        return
    scripts = adapter.active_scripts()
    baseline = commands_in(scripts["home_lighting_apply_" + surface + "_baseline"]["sequence"])
    if service == "scene.turn_on":
        expected_scenes = (
            {owners[surface]["scene"]}
            if owners[surface]["owner"] in ("holiday", "49ers")
            else {e for action, e, _ in baseline if action == "scene.turn_on"}
        )
        if not set(entities).issubset(expected_scenes):
            adapter.render_note(surface, "obsolete_scene_selection", entities)
            return
    elif owners[surface]["owner"] == "daily" and service == "light.turn_on":
        if any((service, entity, parameters) not in baseline for entity in entities):
            adapter.render_note(surface, "obsolete_baseline_parameters", entities)
            return
    scenes = entities if service == "scene.turn_on" else []
    members, metadata = await adapter.hue.read(scenes, [surface])
    refreshed = await adapter.owners()
    if not projection_current(hass, initial) or not same_resolved_owners(refreshed, owners):
        adapter.render_note(surface, "stale_plan", entities)
        return
    group = set(members.get(surface, ()))
    if not group:
        adapter.render_note(surface, "unresolved_membership", entities)
        return
    current = project_owners(hass, owners, members)[surface]
    protected = set(current["manual_entities"])
    if surface == "main_area" and owners["liquor_cabinet"]["owner"] == "door":
        protected.add(LIQUOR)
    # Raw Hue extras are never added to HLM ownership. They may participate in a
    # safe native recall, but are excluded from selective execution.
    managed = group & set(initial["entities"]) if initial else group
    allowed = managed - protected
    allowed = {
        e
        for e in allowed
        if (s := hass.states.get(e)) and s.state not in ("unknown", "unavailable")
    }
    if not allowed:
        adapter.render_note(surface, "no_eligible_automatic_members", sorted(protected))
        return
    await hass.services.async_call(
        "script",
        "home_lighting_rearm_ha_guard",
        {
            "guard_entity": "input_boolean.home_lighting_ha_guard_" + surface,
        },
        blocking=True,
        context=context,
    )

    async def valid():
        current_owners = await adapter.owners()
        return (
            projection_current(hass, initial)
            and same_resolved_owners(current_owners, owners)
            and adapter.active_scripts() == scripts
        )

    def eligible():
        return {
            e
            for e in allowed
            if (s := hass.states.get(e)) and s.state not in ("unknown", "unavailable")
        }

    if not await valid():
        adapter.render_note(surface, "stale_plan", entities)
        return
    if service == "scene.turn_on":
        for scene in scenes:
            info = metadata.get(scene, {})
            actions = info.get("actions", {})
            if info.get("error") or not actions or not set(actions).issubset(group):
                adapter.render_note(surface, "unresolved_scene_actions", [scene])
                continue
            targets = set(actions) & eligible()
            if not targets:
                continue
            # Full unprotected group may keep Hue native dynamic playback, extras
            # included. Any protected/unavailable member forces selective actions.
            all_available = all(
                (s := hass.states.get(e)) and s.state not in ("unknown", "unavailable")
                for e in actions
            )
            if not (set(actions) & protected) and all_available and managed.issubset(eligible()):
                if await valid() and all(
                    (s := hass.states.get(e)) and s.state not in ("unknown", "unavailable")
                    for e in actions
                ):
                    await hass.services.async_call(
                        "scene",
                        "turn_on",
                        {"entity_id": scene, **parameters},
                        blocking=True,
                        context=context,
                    )
                    adapter.render_note(surface, "native_scene", [scene])
            else:
                applied = await adapter.hue.apply_actions(
                    info, protected=set(actions) - targets, valid=valid
                )
                adapter.render_note(surface, "selective_static_scene_actions", applied)
    else:
        # Expand aggregate requests to exact eligible members, never dispatch an
        # aggregate that could touch a protected leaf.
        requested = set()
        for entity in entities:
            if entity in group:
                requested.add(entity)
            else:
                state = hass.states.get(entity)
                children = set(state.attributes.get("entity_id", ())) if state else set()
                if entity in GROUP_ALIASES[surface] or children == group:
                    requested |= group
        for entity in sorted(requested & eligible()):
            if not await valid():
                adapter.render_note(surface, "stale_plan", [entity])
                return
            if entity not in eligible():
                continue
            domain, action = service.split(".")
            await hass.services.async_call(
                domain, action, {"entity_id": entity, **parameters}, blocking=True, context=context
            )
        adapter.render_note(surface, "selective_light_command", sorted(requested & eligible()))
