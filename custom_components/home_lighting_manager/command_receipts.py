"""Bounded, runtime-only causal evidence for explicitly tracked dispatches.

This is metadata, not ownership, a command queue or a dispatcher. Legacy
operation/guard receipts retain their commissioned behavior independently.
"""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite

from .model import Appearance


@dataclass(frozen=True)
class CommandReceipt:
    entity: str
    expected: dict
    context_id: str
    generation: int
    created: datetime
    phase: str = "prepared"


class CommandReceipts:
    """One identity per attempted dispatch; duplicates remain attributable briefly."""

    def __init__(self, limit=128, ttl=8):
        self.limit = limit
        self.ttl = ttl
        self.records = OrderedDict()

    def prepare(self, identity, entity, expected, context_id, generation, now):
        if (
            not isinstance(identity, str) or not 0 < len(identity) <= 128
            or not isinstance(entity, str) or not entity.startswith("light.")
            or not isinstance(context_id, str) or not 0 < len(context_id) <= 128
            or not isinstance(expected, dict) or type(expected.get("on")) is not bool
            or set(expected) - {
                "on", "brightness", "color_temp_kelvin", "xy_color", "rgb_color",
                "hs_color", "effect", "color_mode",
            }
        ):
            raise ValueError("Invalid tracked consequence")
        native = dict(expected)
        for key in ("xy_color", "rgb_color", "hs_color"):
            if key in native and isinstance(native[key], (list, tuple)):
                native[key] = tuple(native[key])
        if not Appearance(**native).is_valid():
            raise ValueError("Invalid native consequence signature")
        if identity in self.records:
            raise ValueError("Consequence identity already registered")
        self.records[identity] = CommandReceipt(
            entity, deepcopy(expected), context_id, generation, now
        )
        while len(self.records) > self.limit:
            self.records.popitem(last=False)

    def cancel(self, identity):
        self.records.pop(identity, None)

    def advance(self, identity, phase, generation, now):
        item = self.records.get(identity)
        if item is None or item.generation != generation:
            raise ValueError("Unknown or obsolete consequence")
        if phase == "submitted" and item.phase == "prepared":
            self.records[identity] = replace(item, phase=phase, created=now)
        elif phase == "complete" and item.phase == "submitted":
            self.records[identity] = replace(item, phase=phase, created=now)
        else:
            raise ValueError("Invalid consequence lifecycle transition")

    def matches(self, entity, actual, context, generation, now):
        self.records = OrderedDict(
            (key, item) for key, item in self.records.items()
            if item.generation == generation and 0 <= (now - item.created).total_seconds()
            <= (30 if item.phase == "submitted" else self.ttl)
        )
        for item in reversed(self.records.values()):
            if item.entity != entity:
                continue
            # A different affirmative HA command is not this dispatch. Context
            # loss is handled by native payload matching, not a blanket guard.
            if context.user_id is not None and context.id != item.context_id:
                continue
            if context.parent_id is not None and (
                context.id != item.context_id and context.parent_id != item.context_id
            ):
                continue
            if signature_matches(item.expected, actual):
                return True
        return False


def signature_matches(expected, actual):
    """Compare only commanded native fields; never convert color representations."""
    for key, wanted in expected.items():
        value = actual.get(key)
        if key in ("on", "effect", "color_mode"):
            if type(value) is not type(wanted) or value != wanted:
                return False
        elif isinstance(wanted, (list, tuple)):
            if not isinstance(value, (list, tuple)) or len(value) != len(wanted):
                return False
            tolerance = 0.001 if key == "xy_color" else 1
            if any(type(v) not in (int, float) or not isfinite(v) or abs(v - w) > tolerance
                   for v, w in zip(value, wanted, strict=True)):
                return False
        elif type(value) not in (int, float) or not isfinite(value) or abs(value - wanted) > (
            50 if key == "color_temp_kelvin" else 1
        ):
            return False
    return True
