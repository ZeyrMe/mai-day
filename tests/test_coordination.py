import importlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import module

Coordination = importlib.import_module(module.__package__ + ".coordination").Coordination


def make(tmp_path):
    p = module.Plugin()
    p.set_plugin_config({"plugin": {"config_version": "0.1.0"}, "volatility_level": "fixed"})
    p.store = module.StateFile(tmp_path / "day.json", module.initial_state())
    p.engine = module.Engine(p.config, p.store)
    p.engine.now = lambda: datetime.fromisoformat("2026-11-27T16:00:00+08:00")
    return p


def test_block_local_jitter_zero_and_locked_negotiation_cannot_be_overridden(tmp_path):
    p = make(tmp_path)
    p.config.preset = "custom"
    p.config.volatility_level = "chaotic"
    p.config.preset_map["custom"].weekdays = [
        module.Block(
            start="16:00",
            end="17:00",
            title="锚点",
            flexibility="locked",
            negotiable=True,
            max_shift_minutes=30,
        ),
        module.Block(start="17:00", end="18:00", title="可挪", flexibility="soft", max_shift_minutes=0),
    ]
    plan = p.engine.plan(p.engine.logical_date(p.engine.now()))
    assert plan["blocks"][0]["start"].endswith("16:00:00+08:00")
    assert plan["blocks"][1]["start"].endswith("17:00:00+08:00")
    availability = p.engine.availability("2026-11-27T16:10:00+08:00", "2026-11-27T16:20:00+08:00")
    assert availability["availability"] == "conflict_locked"
    assert (
        p.engine.availability("2026-11-27T17:10:00+08:00", "2026-11-27T17:20:00+08:00")["availability"]
        == "negotiable"
    )


async def test_catchup_threshold_once_per_sleep_and_never_replyable(tmp_path):
    p = make(tmp_path)
    messages = [
        {"processed_plain_text": f"有效讨论 {i}", "message_info": {"user_info": {"user_id": str(i)}}}
        for i in range(99)
    ]
    ctx = SimpleNamespace(
        message=SimpleNamespace(
            get_by_time_in_chat=AsyncMock(return_value={"success": True, "messages": messages})
        ),
        api=SimpleNamespace(get=AsyncMock(return_value=None)),
        llm=SimpleNamespace(generate=AsyncMock(return_value={"success": True, "response": "爬楼摘要"})),
        maisaka=SimpleNamespace(context=SimpleNamespace(append=AsyncMock(return_value={"success": True}))),
    )
    p._set_context(ctx)
    p.store.data["routes"]["s"] = {"platform": "qq", "group_id": "g", "user_id": "u"}
    await p.coordination.catchup(True, {"sleeping": False})
    await p.coordination.catchup(False, {"sleeping": True})
    await p.coordination.catchup(False, {"sleeping": False})
    assert ctx.llm.generate.await_count == 1
    assert ctx.maisaka.context.append.await_args.kwargs["replyable"] is False
    assert ctx.maisaka.context.append.await_args.kwargs["source_kind"] == "sleep_catchup"


async def test_small_catchup_needs_no_model_and_frequency_restores_only_owned_value(tmp_path):
    p = make(tmp_path)
    p._set_context(
        SimpleNamespace(
            message=SimpleNamespace(
                get_by_time_in_chat=AsyncMock(
                    return_value={"success": True, "messages": [{"processed_plain_text": "一条"}]}
                )
            ),
            api=SimpleNamespace(get=AsyncMock(return_value=None)),
            llm=SimpleNamespace(generate=AsyncMock()),
            frequency=SimpleNamespace(
                get_adjust=AsyncMock(return_value={"success": True, "value": 0.4}),
                set_adjust=AsyncMock(return_value={"success": True}),
            ),
        )
    )
    p.store.data["routes"]["s"] = {"platform": "qq", "group_id": "g"}
    await p.coordination.catchup(True, {"sleeping": False})
    await p.coordination.catchup(False, {"sleeping": True})
    p.ctx.llm.generate.assert_not_awaited()
    p.coordination.owned["s"] = (1, 0.4)
    await p.coordination.restore()
    p.ctx.frequency.set_adjust.assert_awaited_once_with("s", 1)
    p.coordination.owned["s"] = (1, 0.3)
    await p.coordination.restore()
    assert p.ctx.frequency.set_adjust.await_count == 1


async def test_nap_transition_never_sends_morning_or_goodnight(tmp_path):
    p = make(tmp_path)
    p.config.good_night_mode = "proactive"
    p._set_context(SimpleNamespace(send=SimpleNamespace(text=AsyncMock())))
    await p.greet({"sleeping": True, "current_block": {"id": "2026-11-27:nap"}}, {"sleeping": False})
    await p.greet({"sleeping": False}, {"sleeping": True, "block_id": "2026-11-27:nap"})
    p.ctx.send.text.assert_not_awaited()


async def test_sleep_silence_zeros_group_even_when_weights_disabled_and_restores_on_wake(tmp_path):
    p = make(tmp_path)
    p.config.preset = "custom"
    p.config.weights.enabled = False
    p.config.sleep_silence = True
    p.config.preset_map["custom"].weekdays = [
        module.Block(start="08:00", end="23:00", title="自由安排", flexibility="open", sleeping=False),
        module.Block(start="23:00", end="08:00", title="睡觉", flexibility="soft", sleeping=True),
    ]
    current = {"val": 0.7}

    async def get_adjust(stream):
        return {"success": True, "value": current["val"]}

    async def set_adjust(stream, value):
        current["val"] = value
        return {"success": True}

    p._set_context(
        SimpleNamespace(
            api=SimpleNamespace(get=AsyncMock(return_value=None)),
            frequency=SimpleNamespace(
                get_adjust=AsyncMock(side_effect=get_adjust),
                set_adjust=AsyncMock(side_effect=set_adjust),
            ),
        )
    )
    p.store.data["routes"]["g_stream"] = {"platform": "qq", "group_id": "1001", "user_id": "u1"}
    p.store.data["routes"]["p_stream"] = {"platform": "qq", "group_id": "", "user_id": "u2"}

    p.engine.now = lambda: datetime.fromisoformat("2026-11-28T01:00:00+08:00")
    await p.coordination.frequency()
    assert current["val"] == 0.0
    p.ctx.frequency.set_adjust.assert_awaited_once_with("g_stream", 0.0)

    p.engine.now = lambda: datetime.fromisoformat("2026-11-28T10:00:00+08:00")
    await p.coordination.frequency()
    assert current["val"] == 0.7


async def test_observation_group_always_silenced_and_cold_start_message_silences_immediately(tmp_path):
    p = make(tmp_path)
    p.config.preset = "custom"
    p.config.weights.enabled = False
    p.config.sleep_silence = True
    p.config.passive_observation_groups = [module.config_models.Target(platform="qq", group_id="obs1")]
    p.config.preset_map["custom"].weekdays = [
        module.Block(start="08:00", end="23:00", title="自由安排", flexibility="open", sleeping=False),
        module.Block(start="23:00", end="08:00", title="睡觉", flexibility="soft", sleeping=True),
    ]
    p._set_context(
        SimpleNamespace(
            api=AsyncMock(get=AsyncMock(return_value=None)),
            frequency=SimpleNamespace(
                get_adjust=AsyncMock(return_value={"success": True, "value": 0.5}),
                set_adjust=AsyncMock(return_value={"success": True}),
            ),
        )
    )
    # Daytime observation group gets zeroed cleanly.
    p.engine.now = lambda: datetime.fromisoformat("2026-11-28T10:00:00+08:00")
    p.store.data["routes"]["obs_stream"] = {"platform": "qq", "group_id": "obs1", "user_id": "u1"}
    await p.coordination.frequency()
    p.ctx.frequency.set_adjust.assert_awaited_with("obs_stream", 0.0)

    # Cold-start first incoming group message during sleep triggers immediate silence_stream.
    p.engine.now = lambda: datetime.fromisoformat("2026-11-28T01:30:00+08:00")
    msg = {
        "platform": "qq",
        "session_id": "cold_stream",
        "message_id": "m1",
        "message_info": {"group_info": {"group_id": "2002"}, "user_info": {"user_id": "u3"}},
    }
    await p.observe_message(message=msg)
    p.ctx.frequency.set_adjust.assert_any_await("cold_stream", 0.0)
