from copy import deepcopy
from datetime import date

import pytest
from test_engine import at
from test_mai_day.engine import parse
from test_mai_day.runtime import inject, optional_api

pytest_plugins = ["test_engine"]


def test_effective_schedule_has_no_overlapping_slots(engine):
    engine.config.volatility_level = "chaotic"
    rows = engine.effective_blocks(at("2026-11-26T20:00"))
    for previous, current in zip(rows, rows[1:]):
        assert parse(previous["end"]) <= parse(current["start"])


def test_micro_deviations_are_short_and_do_not_erase_original_plan(engine):
    engine.config.volatility_level = "chaotic"
    engine.config.volatility["chaotic"].micro_probability = 1
    plan = deepcopy(engine.plan(date(2026, 11, 26)))
    effective = engine.effective_blocks(at("2026-11-26T20:00"))
    assert any(row["title"] == "自由安排" for row in plan["blocks"])
    for row in effective:
        if row["id"].endswith(":micro"):
            assert (parse(row["end"]) - parse(row["start"])).total_seconds() <= 1200
    assert engine.plan(date(2026, 11, 26)) == plan


def test_invalid_receipt_does_not_destroy_reservation(engine):
    lease = engine.reserve("s")["lease"]
    with pytest.raises(ValueError):
        engine.settle(lease["id"], True, "")
    assert lease["id"] in engine.store.data["leases"]


def test_non_boolean_settlement_does_not_destroy_reservation(engine):
    lease = engine.reserve("s")["lease"]
    with pytest.raises(ValueError, match="布尔值"):
        engine.settle(lease["id"], "false", "receipt")
    assert lease["id"] in engine.store.data["leases"]


def test_equal_receipts_on_different_streams_are_distinct_events(engine):
    first = engine.reserve("one")["lease"]
    second = engine.reserve("two")["lease"]
    engine.settle(first["id"], True, "same-id")
    engine.settle(second["id"], True, "same-id")
    shared = [row for row in engine.store.data["events"] if row["kind"] == "shared"]
    assert len(shared) == 2


def test_sleep_plan_never_jitters_past_hard_bedtime(engine):
    engine.config.hard_bedtime = "22:45"
    plan = engine.plan(date(2026, 11, 26))
    sleep = next(row for row in plan["blocks"] if row["sleeping"])
    assert parse(sleep["start"]) <= at("2026-11-26T22:45")


def test_continuous_awake_limit_does_not_override_locked_appointments(engine):
    engine.store.data["awake_since"] = at("2026-11-25T09:30").isoformat()
    state = engine.state(at("2026-11-26T09:30"))
    assert state["current_block"]["flexibility"] == "locked"
    assert not state["sleeping"]


def test_sleep_debt_compensation_preserves_locked_start(engine):
    engine.config.volatility_level = "natural"
    engine.config.volatility["natural"].jitter_minutes = 0
    assert engine.defer_sleep(at("2026-11-26T22:55"))
    engine.transition(at("2026-11-27T03:00"))
    assert engine.state(at("2026-11-27T07:05"))["sleeping"]
    engine.transition(at("2026-11-27T08:30"))
    assert engine.store.data["sleep_debt_minutes"] == 0
    assert engine.state(at("2026-11-27T09:00"))["current_block"]["flexibility"] == "locked"


def test_injection_replaces_own_transient_item_without_mutating_input():
    original = {"items": [], "item_schema_version": 1}
    first = inject(original, "day", "现在休息", 25)["modified_kwargs"]
    second = inject(first, "day", "现在上课", 25)["modified_kwargs"]
    assert len(second["items"]) == 1
    assert second["items"][0]["parts"][0]["text"] == "现在上课"
    assert original["items"] == []


async def test_optional_api_unwraps_host_envelope_and_business_errors():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    ctx = SimpleNamespace(
        api=SimpleNamespace(
            get=AsyncMock(return_value={"name": "get"}),
            call=AsyncMock(
                return_value={
                    "success": True,
                    "result": {"schema_version": 1, "success": True, "data": {"value": True}},
                }
            ),
        )
    )
    assert await optional_api(ctx, "zeyr.test.get") == {"value": True}
    ctx.api.call.return_value = {
        "success": True,
        "result": {"schema_version": 1, "success": False, "error": "offline"},
    }
    assert await optional_api(ctx, "zeyr.test.get") is None
    ctx.api.get.side_effect = RuntimeError("unavailable")
    assert await optional_api(ctx, "zeyr.test.get") is None
