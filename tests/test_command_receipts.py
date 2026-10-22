"""Additive tracked-dispatch evidence, independent of legacy Spa receipt behavior."""

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import Context

from custom_components.home_lighting_manager.command_receipts import CommandReceipts

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
WHITE = {"on": True, "brightness": 255, "color_temp_kelvin": 4000, "effect": "off"}


@pytest.mark.parametrize("context", [Context(), Context(id="dispatch"),
                                    Context(parent_id="dispatch")])
def test_signature_tracks_context_preserved_or_lost_without_consuming_duplicates(context):
    ledger = CommandReceipts()
    ledger.prepare("white", "light.any_surface", WHITE, "dispatch", 1, NOW)
    for _ in range(3):
        assert ledger.matches("light.any_surface", WHITE, context, 1, NOW)
    assert not ledger.matches("light.sibling", WHITE, context, 1, NOW)


@pytest.mark.parametrize("actual", [
    {**WHITE, "on": False}, {**WHITE, "brightness": 91},
    {**WHITE, "color_temp_kelvin": 2700}, {"on": True},
])
def test_different_or_incomplete_native_payload_is_not_a_consequence(actual):
    ledger = CommandReceipts()
    ledger.prepare("white", "light.any_surface", WHITE, "dispatch", 1, NOW)
    assert not ledger.matches("light.any_surface", actual, Context(), 1, NOW)


def test_independent_explicit_context_wins_even_for_same_payload():
    ledger = CommandReceipts()
    ledger.prepare("white", "light.any_surface", WHITE, "dispatch", 1, NOW)
    assert not ledger.matches("light.any_surface", WHITE, Context(user_id="human"), 1, NOW)
    assert ledger.matches("light.any_surface", WHITE,
                          Context(id="dispatch", user_id="human"), 1, NOW)


def test_cancel_only_removes_unsent_transaction_not_older_valid_receipt():
    ledger = CommandReceipts()
    for identity, desired in (("sent_off", {"on": False}), ("cancelled_white", WHITE)):
        ledger.prepare(identity, "light.any_surface", desired, "dispatch", 1, NOW)
    ledger.cancel("cancelled_white")
    assert not ledger.matches("light.any_surface", WHITE, Context(), 1, NOW)
    assert ledger.matches("light.any_surface", {"on": False}, Context(), 1, NOW)


@pytest.mark.parametrize("generation,age", [(2, 0), (1, 9), (1, -1)])
def test_generation_expiry_and_clock_reversal_cannot_reuse_receipt(generation, age):
    ledger = CommandReceipts()
    ledger.prepare("white", "light.any_surface", WHITE, "dispatch", 1, NOW)
    assert not ledger.matches("light.any_surface", WHITE, Context(), generation,
                              NOW + timedelta(seconds=age))


def test_receipts_bounded_transient_and_defensive_copy():
    ledger = CommandReceipts(limit=2)
    expected = dict(WHITE)
    for i in range(5):
        ledger.prepare(str(i), "light.any_surface", expected, "dispatch", 1, NOW)
    expected["brightness"] = 91
    assert list(ledger.records) == ["3", "4"]
    assert ledger.matches("light.any_surface", WHITE, Context(), 1, NOW)
    assert not CommandReceipts().matches("light.any_surface", WHITE, Context(), 1, NOW)


def test_pending_transport_then_completed_receipt_has_explicit_bounded_lifetime():
    ledger = CommandReceipts()
    ledger.prepare("white", "light.any_surface", WHITE, "dispatch", 1, NOW)
    ledger.advance("white", "submitted", 1, NOW)
    delayed = NOW + timedelta(seconds=10)
    assert ledger.matches("light.any_surface", WHITE, Context(), 1, delayed)
    ledger.advance("white", "complete", 1, delayed)
    assert ledger.matches("light.any_surface", WHITE, Context(), 1, delayed + timedelta(seconds=7))
    assert not ledger.matches("light.any_surface", WHITE, Context(), 1, delayed + timedelta(seconds=9))


@pytest.mark.parametrize("expected", [{"on": True, "brightness": float("nan")},
                                    {"on": True, "effect": "x" * 257},
                                    {"on": True, "xy_color": [0.2] }])
def test_malformed_expected_state_is_not_admitted(expected):
    with pytest.raises(ValueError):
        CommandReceipts().prepare("bad", "light.any", expected, "dispatch", 1, NOW)
