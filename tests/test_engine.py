from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import module
from test_mai_day.config_models import Block, Config, Week
from test_mai_day.engine import Engine, initial_state

ZONE = ZoneInfo("Asia/Shanghai")


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=ZONE)


@pytest.fixture
def engine(tmp_path):
    config = Config(volatility_level="fixed")
    store = module.StateFile(tmp_path / "life.json", initial_state())
    engine = Engine(config, store)
    engine.now = lambda: at("2026-11-26T20:00")
    return engine


def test_restart_and_config_changes_do_not_rewrite_plan(engine):
    before = deepcopy(engine.plan(date(2026, 11, 26)))
    engine.config.preset = "night_owl"
    restarted = Engine(engine.config, module.StateFile(engine.store.path, initial_state()))
    assert restarted.plan(date(2026, 11, 26)) == before


def test_midnight_and_rollover_preserve_overnight_sleep(engine):
    assert engine.logical_date(at("2026-11-27T02:00")) == date(2026, 11, 26)
    assert engine.state(at("2026-11-27T05:30"))["sleeping"]
    assert not engine.state(at("2026-11-27T10:00"))["sleeping"]


def test_locked_appointments_never_jitter(engine):
    engine.config.volatility_level = "chaotic"
    plan = engine.plan(date(2026, 11, 26))
    work = next(b for b in plan["blocks"] if b["title"] == "工作")
    assert work["start"].endswith("09:00:00+08:00")
    assert work["end"].endswith("12:00:00+08:00")
    for row in plan["blocks"]:
        assert datetime.fromisoformat(row["end"]) > datetime.fromisoformat(row["start"])


def test_append_only_events_are_idempotent(engine):
    now = engine.now()
    first = engine.event("test", "原计划", now, event_id="same")
    assert engine.event("test", "替换原计划", now, event_id="same") == first
    assert len(engine.store.data["events"]) == 1


def test_todos_only_self_or_mutual_and_locked_conflicts_are_visible(engine):
    def todo(owner, key):
        return {
            "id": key,
            "ownership": owner,
            "start": at("2026-11-26T09:00").isoformat(),
            "end": at("2026-11-26T10:00").isoformat(),
            "text": owner,
        }

    plan = engine.plan(date(2026, 11, 26), todos=[todo("other", "1"), todo("mutual", "2")])
    assert not any(row["id"] == "todo:1" for row in plan["blocks"])
    assert any(row["id"] == "todo:2" for row in plan["blocks"])
    assert plan["conflicts"]


def test_manual_locked_collision_rejected_without_partial_write(engine):
    with pytest.raises(ValueError, match="冲突"):
        engine.add_manual(
            Block(start="10:00", end="11:00", title="临时约定", flexibility="locked"), at("2026-11-26T08:00")
        )
    assert engine.store.data["manual"] == []
    assert engine.store.data["events"] == []


def test_manual_evening_activity_is_visible(engine):
    row = engine.add_manual(
        Block(start="19:00", end="20:00", title="吃火锅", flexibility="locked"), at("2026-11-26T18:00")
    )
    assert engine.state(at("2026-11-26T19:30"))["current_block"]["id"] == row["id"]


def test_share_reservation_prevents_double_send_and_receipts_are_idempotent(engine):
    first = engine.reserve("stream", engine.now())
    assert first["value"]
    assert not engine.reserve("stream", engine.now())["value"]
    key = first["lease"]["id"]
    assert engine.settle(key, True, "receipt")["sent"]
    assert engine.settle(key, True, "receipt")["sent"]
    assert len(engine.store.data["events"]) == 1
    assert engine.free("stream", engine.now())[1] == "cooldown"


def test_failed_delivery_does_not_consume_share_budget(engine):
    lease = engine.reserve("stream")["lease"]
    engine.settle(lease["id"], False, "")
    assert engine.free("stream")[0]
    assert engine.store.data["events"] == []


def test_observation_group_never_gets_share_permission(engine):
    from test_mai_day.config_models import Target

    engine.config.passive_observation_groups = [Target(group_id="watch")]
    engine.store.data["routes"]["stream"] = {"platform": "qq", "group_id": "watch"}
    assert engine.free("stream")[1] == "observation_only"


def test_deferral_keeps_original_and_obeys_hard_bedtime(engine):
    engine.config.volatility_level = "natural"
    engine.config.volatility["natural"].jitter_minutes = 0
    day = date(2026, 11, 26)
    plan = deepcopy(engine.plan(day))
    assert engine.defer_sleep(at("2026-11-26T22:55"))
    assert engine.plan(day) == plan
    assert not engine.state(at("2026-11-26T23:05"))["sleeping"]
    for minute in range(240):
        engine.defer_sleep(at("2026-11-26T23:05") + timedelta(minutes=minute))
    assert engine.state(at("2026-11-27T03:01"))["sleeping"]
    assert any(event["kind"] == "sleep_deferred" for event in engine.store.data["events"])


def test_continuous_awake_limit_can_recover(engine):
    engine.config.presets["custom"] = Week(
        weekdays=[Block(start="05:00", end="04:59", title="自由")],
        weekend=[Block(start="05:00", end="04:59", title="自由")],
    )
    engine.config.preset = "custom"
    engine.store.data["awake_since"] = at("2026-11-25T12:00").isoformat()
    assert engine.transition(at("2026-11-26T12:01"))[0]["sleeping"]
    assert not engine.transition(at("2026-11-27T12:00"))[0]["sleeping"]


def test_corrupt_state_is_not_silently_erased(tmp_path):
    path = tmp_path / "life.json"
    path.write_text("corrupt")
    with pytest.raises(ValueError):
        module.StateFile(path, initial_state())
    assert path.read_text() == "corrupt"
