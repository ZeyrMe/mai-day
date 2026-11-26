from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
from datetime import timedelta

from maibot_sdk import API, Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.config_widgets import ConfigWidget, ConfigWidgetState
from maibot_sdk.types import HookOrder, ToolParameterInfo

from .config_models import Block, Config
from .coordination import Coordination
from .ecosystem import ChatIdentity, EcosystemMixin
from .engine import Engine, initial_state, parse
from .runtime import StateFile, identity, inject, optional_api, render, result, send_receipt, send_succeeded


class Plugin(EcosystemMixin, MaiBotPlugin):
    config_model = Config
    config_widgets = EcosystemMixin.config_widgets + [
        ConfigWidget(id="wake_now", kind="action", label="临时唤醒"),
        ConfigWidget(id="catchup_status", kind="status", label="爬楼与频率状态"),
        ConfigWidget(id="render_timeline", kind="action", label="刷新今日时间线图"),
        ConfigWidget(
            id="regenerate_plan",
            kind="action",
            label="刷新今日未来软安排",
            confirmation="保留已发生部分和刚性约定，重新投影未来软安排，继续？",
        ),
    ]

    def __init__(self):
        super().__init__()
        self.engine = None
        self.task = None
        self.last_bot = {}
        self.send_grants = {}
        self.command_grants = {}
        self.service_grants = {}
        self.calendar_checked = ""
        self.state_dirty = False
        self.coordination = Coordination(self)
        self.timeline_image = None
        self.widget_task = None
        self.own_user_id = ""

    def console_text(self):
        last = self.store.data.get("last_state", {}) if self.engine else {}
        debt = self.store.data.get("sleep_debt_minutes", 0) if self.engine else 0
        return f"作息预设：{self.config.preset}；波动：{self.config.volatility_level}\n当前：{last}\n睡眠债：{debt} 分钟"

    async def on_load(self):
        self.store = StateFile(self.ctx.paths.data_dir / "life.json", initial_state())
        self.engine = Engine(self.config, self.store)
        with contextlib.suppress(Exception):
            response = await self.ctx.config.get("bot.qq_account", "")
            self.own_user_id = str(response.get("value") or "") if isinstance(response, dict) else ""

        key = self.engine.logical_date(self.engine.now()).isoformat()
        if key in self.store.data["plans"]:
            state, previous = self.engine.transition()
            await self.coordination.catchup(state["sleeping"], previous)
        self.task = asyncio.create_task(self.loop(), name="zeyr-mai-day")

    async def on_unload(self):
        if self.widget_task:
            self.widget_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.widget_task
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.flush_state()
        await self.coordination.restore()
        self.send_grants.clear()
        self.command_grants.clear()
        self.service_grants.clear()

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
        new = key not in self.store.data["plans"]
        routine = (
            await optional_api(self.ctx, "zeyr.mai-profile.get_snapshot", topic="routine") if new else None
        )
        plan = self.engine.plan(day, context, (todos or {}).get("items", []))
        if new:
            plan["routine"] = (routine or {}).get("entries", [])
            if self.config.daily_polish.enabled:
                plan["polish_attempted"] = True
                try:
                    async with asyncio.timeout(self.config.daily_polish.timeout_seconds):
                        response = await self.ctx.llm.generate(
                            render(
                                self.config.daily_polish.instruction,
                                material=json.dumps(plan, ensure_ascii=False),
                            ),
                            model=self.config.daily_polish.model_task,
                        )
                    titles = json.loads(str(response.get("response") or response.get("text") or ""))
                    for row in plan["blocks"]:
                        if row["flexibility"] != "locked" and isinstance(titles.get(row["id"]), str):
                            row["title"] = titles[row["id"]][:100]
                except Exception:
                    self.ctx.logger.warning("每日润色未成功，保留原始标题")
            self.store.save()
        if todos is not None:
            self.engine.sync_calendar((todos or {}).get("items", []), now)
        self.calendar_checked = key

    async def loop(self):
        await asyncio.sleep(3)
        while True:
            try:
                if self.config.enabled:
                    await self.prepare_day()
                    state, previous = self.engine.transition()
                    if previous:
                        await self.coordination.catchup(state["sleeping"], previous)
                        await self.greet(state, previous)
                await self.coordination.frequency()
                self.flush_state()
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
        if not await self.in_scope(message, **kwargs):
            return {"action": "continue"}
        platform, uid, group, stream = identity(message)
        if not stream:
            return {"action": "continue"}
        now = self.engine.now()
        route = self.store.data["routes"].setdefault(stream, {})
        route.update(
            platform=platform, group_id=group, user_id=uid, message_id=str(message.get("message_id") or "")
        )
        bot = await optional_api(self.ctx, "zeyr.mai-circle.is_bot", platform=platform, user_id=uid)
        if not (bot and bot.get("value")):
            route["human_at"] = now.isoformat()
            self.coordination.participant(stream, uid, now)
            last = self.last_bot.get(stream)
            if (
                last
                and now - last <= timedelta(minutes=self.config.engaged_minutes)
                and not self.observation(platform, group)
            ):
                if group or self.config.private_delay_sleep:
                    self.engine.defer_sleep(now, self.config.private_delay_multiplier if not group else 1)
        self.state_dirty = True
        if group:
            await self.coordination.silence_stream(stream)
        return {"action": "continue"}

    def flush_state(self):
        if self.state_dirty:
            self.store.save()
            self.state_dirty = False

    def command_allowed(self, stream):
        return self.command_grants.get(stream, self.engine.now()) > self.engine.now()

    @HookHandler("maisaka.planner.before_request", order=HookOrder.LATE)
    async def planner(self, **kwargs):
        if not await self.in_scope(**kwargs):
            return {"action": "continue"}
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
        if not await self.in_scope(**kwargs):
            return {"action": "continue"}
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
        if not await self.in_scope(stream_id=stream):
            return {"action": "continue"}
        text = str(message.get("processed_plain_text") or "")
        grant = self.send_grants.get((stream, self.text_key(text)))
        granted = grant and parse(grant["expires_at"]) > self.engine.now()
        if granted:
            if grant.get("purpose") == "share" and not self.engine.free(stream, ignore_lease=True)[0]:
                return {"action": "abort", "custom_result": {"reason": "share_window_closed"}}
            info = message.setdefault("message_info", {})
            info.setdefault("additional_config", {})["zeyr_day_lease"] = grant["id"]
            return {"action": "continue", "modified_kwargs": {**kwargs, "message": message}}
        expires = self.service_grants.pop((stream, self.text_key(text)), None)
        if expires and expires > self.engine.now():
            return {"action": "continue"}
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
        if str(state.get("current_block", {}).get("id", "")).endswith(":nap") or str(
            previous.get("block_id", "")
        ).endswith(":nap"):
            return
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
            if (
                not matches
                or self.stream_observation(stream)
                or not await self.target_in_scope(
                    route.get("platform", "qq"), route.get("group_id", ""), route.get("user_id", ""), stream
                )
            ):
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
            sent = send_succeeded(response)
            receipt = send_receipt(response, lease["id"]) if sent else ""
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
            ),
            ToolParameterInfo(name="start", description="查档期开始 ISO 时间", required=False, default=""),
            ToolParameterInfo(name="end", description="查档期结束 ISO 时间", required=False, default=""),
            ToolParameterInfo(
                name="event_text", description="记录刚发生的生活变化", required=False, default=""
            ),
        ],
        visibility="visible",
    )
    async def query_schedule(self, scope="all", start="", end="", event_text="", **kwargs):
        if not await self.in_scope(**kwargs):
            return result(error="当前会话不在插件生效范围")
        if scope not in ("all", "plan", "current", "events"):
            return result(error="scope 应为 all / plan / current / events")
        now = self.engine.now()
        if start:
            try:
                return result(self.engine.availability(start, end))
            except ValueError as exc:
                return result(error=str(exc))
        if event_text:
            chat = ChatIdentity.from_message(kwargs.get("message") or kwargs, kwargs.get("stream_id") or "")
            route = self.store.data["routes"].get(chat.stream_id, {})
            message_id = route.get("message_id", "")
            if not chat.user_id or chat.user_id != route.get("user_id") or not message_id:
                return result(error="记录动态需要真实当前消息")
            return result(
                self.engine.event(
                    "self_note", event_text[:1000], now, event_id=f"note:{chat.stream_id}:{message_id}"
                )
            )
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

    @API("authorize_service_send", public=True, version="1")
    async def api_service_send(self, stream_id: str, text: str, **kwargs):
        # Code-only API, never exposed as a model Tool. Authorization expires after 30 seconds.
        if not stream_id or not text.strip() or len(text) > 10000:
            return result(error="需要会话和 1–10000 字的服务消息")
        now = self.engine.now()
        self.service_grants = {key: expiry for key, expiry in self.service_grants.items() if expiry > now}
        self.service_grants[(stream_id, self.text_key(text))] = now + timedelta(
            seconds=self.config.service_grant_seconds
        )
        return result({"value": True})

    @API("revoke_service_send", public=True, version="1")
    async def api_revoke_service_send(self, stream_id: str, text: str, **kwargs):
        self.service_grants.pop((stream_id, self.text_key(text)), None)
        return result({"value": True})

    @API("check_availability", public=True, version="1")
    async def api_availability(self, start, end="", **kwargs):
        try:
            return result(self.engine.availability(start, end))
        except ValueError as exc:
            return result(error=str(exc))

    async def get_config_widget_state(self):
        states = await super().get_config_widget_state()
        return {
            **states,
            "wake_now": ConfigWidgetState(disabled=not bool(self.engine)),
            "render_timeline": ConfigWidgetState(
                disabled=not self.engine or bool(self.widget_task and not self.widget_task.done())
            ),
            "regenerate_plan": ConfigWidgetState(disabled=not self.engine),
            "catchup_status": ConfigWidgetState(
                image=self.timeline_image,
                text=json.dumps(
                    {
                        "catchup": self.coordination.status,
                        "frequency": {stream: value[1] for stream, value in self.coordination.owned.items()},
                    },
                    ensure_ascii=False,
                )[:2000],
            ),
        }

    async def render_timeline(self):
        try:
            rows = self.engine.effective_blocks(self.engine.now())
            body = "".join(
                f"<tr><td>{html.escape(row['start'][11:16])}–{html.escape(row['end'][11:16])}</td><td>{html.escape(row['title'])}</td><td>{html.escape(row['flexibility'])}</td></tr>"
                for row in rows
                if parse(row["end"]) > self.engine.now()
            )
            image = await self.ctx.render.html2png(
                "<html><meta charset='utf-8'><body style='font:18px sans-serif;padding:24px;background:#f4f5f7'><h2>今日后续时间线</h2><table cellspacing='12'>"
                + body
                + "</table></body></html>",
                allow_network=False,
            )
            if isinstance(image, dict) and image.get("image_base64"):
                self.timeline_image = "data:image/png;base64," + image["image_base64"]
        except Exception:
            self.ctx.logger.warning("时间线渲染失败，继续提供文字状态")

    async def on_config_widget_action(self, action_id):
        if action_id == "render_timeline" and self.engine:
            if not self.widget_task or self.widget_task.done():
                self.widget_task = asyncio.create_task(self.render_timeline())
            return
        if action_id == "regenerate_plan" and self.engine:
            now = self.engine.now()
            day = self.engine.logical_date(now)
            self.engine.replan_future(day, now)
            return
        if action_id == "wake_now" and self.engine:
            self.store.data["wake_until"] = (
                self.engine.now() + timedelta(minutes=self.config.wake_minutes)
            ).isoformat()
            self.store.save()
            return
        await super().on_config_widget_action(action_id)

    @API("get_state", public=True, version="1")
    async def api_state(self, stream_id="", **kwargs):
        state = self.engine.state()
        owned_scope = not stream_id or await self.in_scope(stream_id=stream_id)
        is_obs = bool(stream_id and self.stream_observation(stream_id))
        sleeping_silent = bool(state["sleeping"] and self.config.sleep_silence)
        return result(
            {
                **state,
                "frequency_owner": bool(
                    self.config.enabled
                    and (is_obs or (owned_scope and (self.config.weights.enabled or sleeping_silent)))
                ),
            }
        )

    @API("is_free_for_sharing", public=True, version="1")
    async def api_free(self, stream_id="", group_id="", **kwargs):
        if not await self.in_scope(stream_id=stream_id or group_id):
            return result({"value": False, "reason": "outside_scope"})
        if not self.target_active(stream_id or group_id):
            return result({"value": False, "reason": "inactive_target"})
        allowed, reason = self.engine.free(stream_id or group_id)
        return result({"value": allowed, "reason": reason})

    @API("append_dynamic_event", public=True, version="1")
    async def api_event(self, text: str, source: str, event_id: str, **kwargs):
        if not event_id or not text.strip() or len(text) > 4000:
            return result(error="需要 event_id 与 1–4000 字的 text")
        return result(
            self.engine.event("external", text, self.engine.now(), source=source, event_id=event_id)
        )

    def target_active(self, stream):
        route = self.store.data["routes"].get(stream, {})
        return bool(
            route.get("human_at")
            and self.engine.now() - parse(route["human_at"])
            <= timedelta(minutes=self.config.active_group_recent_minutes)
        )

    @API("reserve_share", public=True, version="1")
    async def api_reserve(self, stream_id: str, **kwargs):
        if not self.target_active(stream_id):
            return result({"value": False, "reason": "inactive_target"})
        if not await self.in_scope(stream_id=stream_id):
            return result({"value": False, "reason": "outside_scope"})
        return result(self.engine.reserve(stream_id))

    @API("authorize_share", public=True, version="1")
    async def api_authorize(self, lease_id: str, text: str, **kwargs):
        lease = self.store.data["leases"].get(lease_id)
        if not lease or parse(lease["expires_at"]) <= self.engine.now():
            return result(error="发送租约不存在或已过期")
        if not text.strip() or len(text) > 10000:
            return result(error="发送内容需要 1–10000 字")
        if not await self.in_scope(stream_id=lease["stream_id"]):
            return result({"value": False, "reason": "outside_scope"})
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
        if not await self.in_scope(message, stream_id=stream_id):
            return False, "当前会话不在插件生效范围", 1
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
        expiry = self.engine.now() + timedelta(seconds=self.config.service_grant_seconds)
        key = (stream, self.text_key(text))
        self.send_grants[key] = {"id": "command", "expires_at": expiry.isoformat()}
        try:
            await self.ctx.send.text(text, stream)
        finally:
            self.send_grants.pop(key, None)

    @Command("wake", pattern=r"^/唤醒$")
    async def wake(self, message=None, stream_id="", **kwargs):
        if not await self.in_scope(message, stream_id=stream_id):
            return False, "当前会话不在插件生效范围", 1
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
