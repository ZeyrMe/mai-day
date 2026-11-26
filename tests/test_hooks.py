from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import module
from test_mai_day.engine import Engine, initial_state


@pytest.fixture
def plugin(tmp_path):
    plugin = module.Plugin()
    plugin.set_plugin_config({"plugin": {"config_version": "0.1.0"}, "volatility_level": "fixed"})
    plugin.store = module.StateFile(tmp_path / "life.json", initial_state())
    plugin.engine = Engine(plugin.config, plugin.store)
    plugin._set_context(
        SimpleNamespace(
            send=SimpleNamespace(text=AsyncMock(return_value={"sent": True, "message_id": "sent"}))
        )
    )
    return plugin


async def test_sleep_does_not_abort_incoming_messages(plugin):
    plugin.engine.state = lambda *args: {"sleeping": True}
    assert await plugin.observe_message(message={"message_id": "m"}) == {"action": "continue"}


async def test_sleep_blocks_all_outgoing_and_filters_reply_tool(plugin):
    plugin.engine.state = lambda *args: {"sleeping": True, "phase": "sleep", "anchor": "休息"}
    result = await plugin.before_send(message={"session_id": "s", "processed_plain_text": "普通回复"})
    assert result["action"] == "abort"
    result = await plugin.planner(
        session_id="s",
        items=[],
        item_schema_version=1,
        tool_definitions=[
            {"function": {"name": name}} for name in ["reply", "send_emoji", "query_today_schedule"]
        ],
    )
    assert [row["function"]["name"] for row in result["modified_kwargs"]["tool_definitions"]] == [
        "query_today_schedule"
    ]


async def test_exact_command_output_grant_does_not_open_other_messages(plugin):
    plugin.engine.state = lambda *args: {"sleeping": True, "phase": "sleep"}
    expiry = plugin.engine.now() + timedelta(seconds=30)
    plugin.send_grants[("s", plugin.text_key("指令完成"))] = {
        "id": "command",
        "expires_at": expiry.isoformat(),
    }
    assert (await plugin.before_send(message={"session_id": "s", "processed_plain_text": "指令完成"}))[
        "action"
    ] == "continue"
    assert (await plugin.before_send(message={"session_id": "s", "processed_plain_text": "其他话"}))[
        "action"
    ] == "abort"
    assert (await plugin.before_send(message={"session_id": "another", "processed_plain_text": "指令完成"}))[
        "action"
    ] == "abort"


async def test_missing_circle_uses_local_admin_without_prompt_exposure(plugin):
    from test_mai_day.config_models import Target

    plugin.config.admins = [Target(user_id="SECRET_ADMIN")]
    assert await plugin.is_admin("qq", "SECRET_ADMIN")
    assert "SECRET_ADMIN" not in str(await plugin.api_state())


async def test_disabled_plugin_does_not_accept_admin_commands(plugin):
    from test_mai_day.config_models import Target

    plugin.config.admins = [Target(user_id="admin")]
    plugin.config.enabled = False
    assert not await plugin.is_admin("qq", "admin")
    state, _ = plugin.engine.transition()
    assert state["phase"] == "disabled"


async def test_generated_greeting_is_settled_only_after_actual_send(plugin):
    plugin.ctx.maisaka = SimpleNamespace(
        reply=SimpleNamespace(generate=AsyncMock(return_value={"success": True, "text": "自然问候"}))
    )
    state = plugin.engine.state()
    await plugin.send_greeting("s", "早安", state, "greeting")
    assert plugin.store.data["greetings"]["greeting"] == "sent"
    assert plugin.store.data["shared_at"]["s"]
    assert plugin.send_grants == {}


async def test_failed_greeting_is_not_marked_delivered(plugin):
    plugin.ctx.maisaka = SimpleNamespace(
        reply=SimpleNamespace(generate=AsyncMock(return_value={"success": True, "text": "自然问候"}))
    )
    plugin.ctx.send.text.return_value = {"success": False, "sent": False}
    await plugin.send_greeting("s", "早安", plugin.engine.state(), "greeting")
    assert "greeting" not in plugin.store.data["greetings"]
    assert "s" not in plugin.store.data["shared_at"]
    assert not plugin.store.data["leases"]


async def test_share_authorization_rechecks_sleep_at_actual_send(plugin):
    lease = plugin.engine.reserve("s", purpose="greeting:test")["lease"]
    lease["purpose"] = "share"
    plugin.engine.free = lambda *args, **kwargs: (True, "open")
    assert (await plugin.api_authorize(lease["id"], "见闻"))["data"]["value"]
    plugin.engine.free = lambda *args, **kwargs: (False, "busy")
    response = await plugin.before_send(message={"session_id": "s", "processed_plain_text": "见闻"})
    assert response["action"] == "abort"


@pytest.mark.parametrize("anchor,limit", [("", 25), ("休息", 0)])
async def test_empty_hint_retains_sleep_tool_filter(plugin, anchor, limit):
    plugin.engine.state = lambda *args: {"sleeping": True, "phase": "sleep", "anchor": anchor}
    plugin.config.hint_chars = limit
    original = [{"function": {"name": name}} for name in ["reply", "send_emoji", "smart_fetch"]]
    response = await plugin.planner(session_id="s", items=[], tool_definitions=original)
    assert response["modified_kwargs"]["tool_definitions"] == original[2:]


async def test_service_authorization_is_exact_one_use_and_observation_still_wins(plugin):
    from test_mai_day.config_models import Target

    plugin.engine.state = lambda *args: {"sleeping": True, "phase": "sleep"}
    await plugin.api_service_send("s", "到期提醒")
    assert (await plugin.before_send(message={"session_id": "other", "processed_plain_text": "到期提醒"}))[
        "action"
    ] == "abort"
    assert (await plugin.before_send(message={"session_id": "s", "processed_plain_text": "别的内容"}))[
        "action"
    ] == "abort"
    assert (await plugin.before_send(message={"session_id": "s", "processed_plain_text": "到期提醒"}))[
        "action"
    ] == "continue"
    assert (await plugin.before_send(message={"session_id": "s", "processed_plain_text": "到期提醒"}))[
        "action"
    ] == "abort"
    plugin.config.passive_observation_groups = [Target(group_id="observe")]
    await plugin.api_service_send("s", "到期提醒")
    assert (
        await plugin.before_send(
            message={
                "session_id": "s",
                "platform": "qq",
                "message_info": {"group_info": {"group_id": "observe"}},
                "processed_plain_text": "到期提醒",
            }
        )
    )["action"] == "abort"


async def test_service_authorization_expires_and_can_be_revoked(plugin):
    plugin.engine.state = lambda *args: {"sleeping": True, "phase": "sleep"}
    await plugin.api_service_send("s", "响应")
    plugin.service_grants[("s", plugin.text_key("响应"))] = plugin.engine.now() - timedelta(seconds=1)
    assert (await plugin.before_send(message={"session_id": "s", "processed_plain_text": "响应"}))[
        "action"
    ] == "abort"
    await plugin.api_service_send("s", "响应")
    await plugin.api_revoke_service_send("s", "响应")
    assert not plugin.service_grants


async def test_receiving_messages_batches_route_persistence(plugin, monkeypatch):
    from unittest.mock import Mock

    save = Mock(wraps=plugin.store.save)
    monkeypatch.setattr(plugin.store, "save", save)
    message = {"platform": "qq", "session_id": "s", "message_info": {"user_info": {"user_id": "u"}}}
    for _ in range(10):
        await plugin.observe_message(message=message)
    save.assert_not_called()
    assert plugin.store.data["routes"]["s"]["human_at"]
    plugin.flush_state()
    save.assert_called_once()
    plugin.flush_state()
    save.assert_called_once()
    await plugin.observe_message(message=message)
    await plugin.on_unload()
    assert save.call_count == 2
