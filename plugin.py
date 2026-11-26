from __future__ import annotations

import asyncio
import contextlib
import hashlib
from datetime import timedelta

from maibot_sdk import API, Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import HookOrder, ToolParameterInfo

from .config_models import Block, Config
from .engine import Engine, initial_state, parse
from .runtime import StateFile, identity, inject, optional_api, render, result


class Plugin(MaiBotPlugin):
    config_model = Config

    def __init__(self):
        super().__init__()
        self.engine = None
        self.task = None
        self.last_bot = {}
        self.send_grants = {}
        self.command_grants = {}
        self.calendar_checked = ""

    async def on_load(self):
        self.store = StateFile(self.ctx.paths.data_dir / "life.json", initial_state())
        self.engine = Engine(self.config, self.store)
        await self.prepare_day()
        self.engine.transition()
        self.task = asyncio.create_task(self.loop(), name="zeyr-mai-day")

    async def on_unload(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.send_grants.clear()
        self.command_grants.clear()

    async def on_config_update(self, scope, config_data, version):
        if scope == "self" and self.engine:
            self.engine.config = self.config
            self.engine.zone = __import__("zoneinfo").ZoneInfo(self.config.timezone)
            self.calendar_checked = ""

    async def prepare_day(self):
        now = self.engine.now()
        day = self.engine.logical_date(now)
        key = day.isoformat()
        context = await optional_api(self.ctx, "zeyr.mai-calendar.get_day_context", date=key)
        todos = await optional_api(self.ctx, "zeyr.mai-calendar.get_schedulable_todos", date=key)
        self.engine.plan(day, context, (todos or {}).get("items", []))
        if todos is not None:
            self.engine.sync_calendar((todos or {}).get("items", []), now)
        self.calendar_checked = key

    async def loop(self):
        while True:
            try:
                if self.config.enabled:
                    await self.prepare_day()
                    state, previous = self.engine.transition()
                    if previous:
                        await self.greet(state, previous)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.ctx.logger.exception("生活调度失败；保留现有状态，下轮重试")
            await asyncio.sleep(self.config.tick_seconds)

    def observation(self, platform, group_id):
        return bool(group_id) and any(
            target.platform == platform and target.group_id == group_id
            for target in self.config.passive_observation_groups
        )

    def stream_observation(self, stream_id):
        route = self.store.data["routes"].get(stream_id, {})
        return self.observation(route.get("platform", ""), route.get("group_id", ""))

    async def is_admin(self, platform, user_id):
        if not self.config.enabled:
            return False
        if any(target.platform == platform and target.user_id == user_id for target in self.config.admins):
            return True
        external = await optional_api(
            self.ctx, "zeyr.mai-circle.is_admin", platform=platform, user_id=user_id
        )
        return bool(external and external.get("value"))

    @HookHandler("chat.receive.after_process")
    async def observe_message(self, message=None, **kwargs):
        # Never abort incoming messages. Only a genuinely participating Bot can defer sleep.
        if not self.config.enabled or not message:
            return {"action": "continue"}
        platform, uid, group, stream = identity(message)
        if not stream:
            return {"action": "continue"}
        now = self.engine.now()
        route = self.store.data["routes"].setdefault(stream, {})
        route.update(platform=platform, group_id=group, user_id=uid)
        bot = await optional_api(self.ctx, "zeyr.mai-circle.is_bot", platform=platform, user_id=uid)
        if not (bot and bot.get("value")):
            route["human_at"] = now.isoformat()
            last = self.last_bot.get(stream)
            if (
                last
                and now - last <= timedelta(minutes=self.config.engaged_minutes)
                and not self.observation(platform, group)
            ):
                self.engine.defer_sleep(now)
        self.store.save()
        return {"action": "continue"}

    def command_allowed(self, stream):
        return self.command_grants.get(stream, self.engine.now()) > self.engine.now()

    @HookHandler("maisaka.planner.before_request", order=HookOrder.LATE)
    async def planner(self, **kwargs):
        if not self.config.enabled:
            return {"action": "continue"}
        state = self.engine.state()
        stream = str(kwargs.get("session_id") or "")
        if (state["sleeping"] and self.config.sleep_silence) or self.stream_observation(stream):
            kwargs["tool_definitions"] = [
                tool
                for tool in kwargs.get("tool_definitions", [])
                if tool.get("function", {}).get("name") not in ("reply", "send_emoji")
            ]
        return inject(kwargs, "mai-day", state["anchor"], self.config.hint_chars)

    @HookHandler("maisaka.replyer.before_request", order=HookOrder.LATE)
    async def replyer(self, **kwargs):
        if not self.config.enabled:
            return {"action": "continue"}
        # Background document generation does not itself speak; the send guard still applies.
        document = kwargs.get("request_type") == "plugin.replyer"
        state = self.engine.state()
        stream = str(kwargs.get("session_id") or kwargs.get("stream_id") or "")
        if not document and (
            self.stream_observation(stream) or (state["sleeping"] and self.config.sleep_silence)
        ):
            return {"action": "abort", "custom_result": {"reason": state["phase"]}}
        return inject(kwargs, "mai-day", state["anchor"], self.config.hint_chars)

    @staticmethod
    def text_key(text):
        return hashlib.sha256(text.encode()).hexdigest()

    @HookHandler("send_service.before_send", order=HookOrder.LATE)
    async def before_send(self, message=None, **kwargs):
        if not self.config.enabled or not message:
            return {"action": "continue"}
        platform, _, group, stream = identity(message)
        if self.observation(platform, group) or self.stream_observation(stream):
            return {"action": "abort", "custom_result": {"reason": "observation_only"}}
        text = str(message.get("processed_plain_text") or "")
        grant = self.send_grants.get((stream, self.text_key(text)))
        granted = grant and parse(grant["expires_at"]) > self.engine.now()
        if granted:
            if grant.get("purpose") == "share" and not self.engine.free(stream, ignore_lease=True)[0]:
                return {"action": "abort", "custom_result": {"reason": "share_window_closed"}}
            info = message.setdefault("message_info", {})
            info.setdefault("additional_config", {})["zeyr_day_lease"] = grant["id"]
            return {"action": "continue", "modified_kwargs": {**kwargs, "message": message}}
        if self.config.sleep_silence and self.engine.state()["sleeping"]:
            return {"action": "abort", "custom_result": {"reason": "sleep"}}
        return {"action": "continue"}

    @HookHandler("send_service.after_send")
    async def after_send(self, message=None, sent=False, **kwargs):
        if self.config.enabled and sent and message:
            _, _, _, stream = identity(message)
            self.last_bot[stream] = self.engine.now()
        return {"action": "continue"}

    async def greet(self, state, previous):
        # Catch sleep transitions within the greeting window, never replay old transitions after restart.
        changed = state["sleeping"] != previous["sleeping"]
        if not changed:
            return
        kind = "晚安" if state["sleeping"] else "早安"
        mode = self.config.good_night_mode if state["sleeping"] else self.config.good_morning_mode
        if mode != "proactive":
            return
        now = self.engine.now()
        for stream, route in list(self.store.data["routes"].items()):
            matches = any(
                target.platform == route.get("platform")
                and (
                    (target.group_id and target.group_id == route.get("group_id"))
                    or (
                        not target.group_id
                        and not route.get("group_id")
                        and target.user_id == route.get("user_id")
                    )
                )
                for target in self.config.targets
            )
            if not matches or self.stream_observation(stream):
                continue
            if not route.get("human_at") or now - parse(route["human_at"]) > timedelta(
                minutes=self.config.active_group_recent_minutes
            ):
                continue
            key = f"{state['logical_date']}:{kind}:{stream}"
            if key in self.store.data["greetings"]:
                continue
            await self.send_greeting(stream, kind, state, key)

    async def send_greeting(self, stream, kind, state, key):
        # Use a separate Replyer writing request, then send the exact generated text with a short-lived grant.
        # This avoids treating proactive.trigger's queued=True as a delivery receipt.
        lease = self.engine.reserve(stream, purpose=f"greeting:{kind}")["lease"]
        sent, receipt = False, ""
        grant_key = None
        try:
            response = await self.ctx.maisaka.reply.generate(
                stream,
                reference=state["anchor"],
                format_instruction=render(self.config.greeting_instruction, kind=kind),
                timeout_ms=60000,
            )
            if not isinstance(response, dict) or not response.get("success") or not response.get("text"):
                return
            current = self.engine.state()
            if current["sleeping"] != state["sleeping"] or parse(lease["expires_at"]) <= self.engine.now():
                return
            text = response["text"].strip()
            grant_key = (stream, self.text_key(text))
            self.send_grants[grant_key] = lease
            response = await self.ctx.send.text(
                text, stream, return_details=True, sync_to_maisaka_history=True
            )
            sent = bool(isinstance(response, dict) and response.get("success") and response.get("sent"))
            receipt = (
                str((response.get("message_id") if isinstance(response, dict) else "") or lease["id"])
                if sent
                else ""
            )
            if sent:
                self.store.data["greetings"][key] = receipt
        finally:
            if grant_key:
                self.send_grants.pop(grant_key, None)
            self.engine.settle(lease["id"], sent, receipt)

    @Tool(
        "query_today_schedule",
        description="查询今日原始日程、有效安排或持续动态；不会把整张表常驻注入。",
        parameters=[
            ToolParameterInfo(
                name="scope", description="plan / current / events / all", required=False, default="all"
            )
        ],
        visibility="visible",
    )
    async def query_schedule(self, scope="all", **kwargs):
        if scope not in ("all", "plan", "current", "events"):
            return result(error="scope 应为 all / plan / current / events")
        now = self.engine.now()
        data = {}
        if scope in ("all", "plan"):
            data["plan"] = self.engine.plan(self.engine.logical_date(now))
        if scope in ("all", "current"):
            data["state"] = self.engine.state(now)
            data["effective_blocks"] = self.engine.effective_blocks(now)
        if scope in ("all", "events"):
            cutoff = now - timedelta(days=self.config.window_days)
            data["events"] = [row for row in self.store.data["events"] if parse(row["at"]) >= cutoff]
        return result(data)

    @API("get_state", public=True, version="1")
    async def api_state(self, **kwargs):
        return result(self.engine.state())

    @API("is_free_for_sharing", public=True, version="1")
    async def api_free(self, stream_id="", group_id="", **kwargs):
        allowed, reason = self.engine.free(stream_id or group_id)
        return result({"value": allowed, "reason": reason})

    @API("append_dynamic_event", public=True, version="1")
    async def api_event(self, text: str, source: str, event_id: str, **kwargs):
        if not event_id or not text.strip() or len(text) > 4000:
            return result(error="需要 event_id 与 1–4000 字的 text")
        return result(
            self.engine.event("external", text, self.engine.now(), source=source, event_id=event_id)
        )

    @API("reserve_share", public=True, version="1")
    async def api_reserve(self, stream_id: str, **kwargs):
        return result(self.engine.reserve(stream_id))

    @API("authorize_share", public=True, version="1")
    async def api_authorize(self, lease_id: str, text: str, **kwargs):
        lease = self.store.data["leases"].get(lease_id)
        if not lease or parse(lease["expires_at"]) <= self.engine.now():
            return result(error="发送租约不存在或已过期")
        if not text.strip() or len(text) > 10000:
            return result(error="发送内容需要 1–10000 字")
        allowed, reason = self.engine.free(lease["stream_id"], ignore_lease=True)
        if not allowed:
            return result({"value": False, "reason": reason})
        self.send_grants[(lease["stream_id"], self.text_key(text))] = lease
        return result({"value": True, "expires_at": lease["expires_at"]})

    @API("settle_share", public=True, version="1")
    async def api_settle(self, lease_id: str, sent: bool, receipt_id: str = "", **kwargs):
        try:
            return result(self.engine.settle(lease_id, sent, receipt_id))
        except ValueError as exc:
            return result(error=str(exc))

    @Command(
        "schedule_add",
        pattern=r"^/日程\s*\+\s*(?P<start>\d{2}:\d{2})(?:-(?P<end>\d{2}:\d{2}))?\s+(?P<title>.+?)(?:\s+\[(?P<flex>locked|soft|open)\])?$",
    )
    async def schedule_add(self, message=None, matched_groups=None, stream_id="", **kwargs):
        platform, uid, _, _ = identity(message or {})
        if not await self.is_admin(platform, uid):
            return False, "需要管理员权限", 1
        groups = matched_groups or {}
        try:
            end = groups.get("end")
            if not end:
                start = self.engine.wall(self.engine.logical_date(self.engine.now()), groups["start"])
                end = (start + timedelta(hours=1)).strftime("%H:%M")
            block = Block(
                start=groups["start"],
                end=end,
                title=groups["title"],
                flexibility=groups.get("flex") or "soft",
            )
            self.engine.add_manual(block, self.engine.now())
            await self.command_response("日程已添加", stream_id)
            return True, "日程已添加", 2
        except (ValueError, KeyError) as exc:
            await self.command_response(str(exc), stream_id)
            return False, str(exc), 1

    async def command_response(self, text, stream):
        # Exact output grant for this validated command; never a stream-wide bypass.
        expiry = self.engine.now() + timedelta(seconds=30)
        key = (stream, self.text_key(text))
        self.send_grants[key] = {"id": "command", "expires_at": expiry.isoformat()}
        try:
            await self.ctx.send.text(text, stream)
        finally:
            self.send_grants.pop(key, None)

    @Command("wake", pattern=r"^/唤醒$")
    async def wake(self, message=None, stream_id="", **kwargs):
        platform, uid, _, _ = identity(message or {})
        if not await self.is_admin(platform, uid):
            return False, "需要管理员权限", 1
        until = self.engine.now() + timedelta(minutes=self.config.wake_minutes)
        self.store.data["wake_until"] = until.isoformat()
        self.engine.event("admin_wake", until.isoformat(), self.engine.now())
        await self.command_response("临时唤醒已生效", stream_id)
        return True, "临时唤醒已生效", 2


def create_plugin():
    return Plugin()
