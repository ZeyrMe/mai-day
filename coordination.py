"""Background catch-up and reversible single-writer frequency coordination."""

import asyncio
import contextlib
import json
import math
from datetime import timedelta

from .engine import parse
from .runtime import identity, optional_api, render


class Coordination:
    def __init__(self, plugin):
        self.plugin = plugin
        self.owned = {}
        self.users = {}
        self.status = {}
        self.means = {}

    def participant(self, stream, user, now):
        config = self.plugin.config.weights
        users = self.users.setdefault(stream, {})
        users[user] = now
        cutoff = now - timedelta(minutes=config.participant_window_minutes)
        self.users[stream] = {uid: at for uid, at in users.items() if at > cutoff}
        limit = self.plugin.config.scope.max_tracked_streams
        while len(self.users) > limit:
            del self.users[next(iter(self.users))]

    async def frequency(self):
        p = self.plugin
        if not p.config.enabled or not p.config.weights.enabled:
            await self.restore()
            return
        state = p.engine.state()
        block = state["current_block"]
        day = block.get("reply_weight")
        if day is None or day < 0:
            day = getattr(p.config.weights, block["flexibility"])
        if state["sleeping"]:
            day = 0
        elif p.config.low_energy_hint and p.config.low_energy_hint in state["anchor"]:
            day *= p.config.weights.low_energy
        for stream, route in list(p.store.data["routes"].items()):
            if p.stream_observation(stream) or not await p.target_in_scope(
                route.get("platform", "qq"), route.get("group_id", ""), route.get("user_id", ""), stream
            ):
                await self.restore(stream)
                continue
            now = p.engine.now()
            users = [
                uid
                for uid, at in self.users.get(stream, {}).items()
                if now - at < timedelta(minutes=p.config.weights.participant_window_minutes)
            ]
            relation = await optional_api(
                p.ctx,
                "zeyr.mai-circle.get_chat_weight",
                platform=route.get("platform", "qq"),
                user_ids=users,
                scope_kind="group" if route.get("group_id") else "private",
            )
            weight = float((relation or {}).get("weight", 1))
            if p.config.weights.normalize_circle:
                mean = self.means.get(stream, 1)
                mean += p.config.weights.normalization_alpha * (weight - mean)
                self.means[stream] = mean
                weight /= mean
            desired = (
                max(p.config.weights.min_adjust, min(p.config.weights.max_adjust, day * weight)) if day else 0
            )
            current = await p.ctx.frequency.get_adjust(stream)
            if not isinstance(current, dict) or not current.get("success"):
                continue
            prior = self.owned.get(stream)
            original = prior[0] if prior else float(current["value"])
            sent = await p.ctx.frequency.set_adjust(stream, desired)
            if isinstance(sent, dict) and sent.get("success"):
                self.owned[stream] = (original, desired)
                p.store.data.setdefault("frequency_adjust", {})[stream] = desired

    async def restore(self, only=None):
        for stream, (original, written) in list(self.owned.items()):
            if only and stream != only:
                continue
            with contextlib.suppress(Exception):
                current = await self.plugin.ctx.frequency.get_adjust(stream)
                if current.get("success") and math.isclose(float(current["value"]), written):
                    await self.plugin.ctx.frequency.set_adjust(stream, original)
            self.owned.pop(stream, None)
            self.plugin.store.data.setdefault("frequency_adjust", {}).pop(stream, None)

    async def catchup(self, sleeping, previous):
        p = self.plugin
        now = p.engine.now()
        if sleeping:
            if not previous or not previous["sleeping"]:
                p.store.data["sleep_started_at"] = now.isoformat()
                p.store.data["catchup_pending"] = False
                p.store.save()
            return
        if previous and previous["sleeping"]:
            p.store.data["catchup_pending"] = True
            p.store.data["wake_at"] = now.isoformat()
            p.store.save()
        if not p.store.data.get("catchup_pending"):
            return
        failed = False
        start = p.store.data.get("sleep_started_at")
        if not start or not p.config.catchup.enabled:
            return
        for stream, route in list(p.store.data["routes"].items()):
            key = f"{stream}:{start}"
            if key in p.store.data.setdefault("catchup_done", {}):
                continue
            if p.stream_observation(stream) or not await p.target_in_scope(
                route.get("platform", "qq"), route.get("group_id", ""), route.get("user_id", ""), stream
            ):
                continue
            try:
                async with asyncio.timeout(p.config.catchup.timeout_seconds):
                    response = await p.ctx.message.get_by_time_in_chat(
                        stream,
                        str(parse(start).timestamp()),
                        str(parse(p.store.data.get("wake_at") or now.isoformat()).timestamp()),
                        limit=p.config.catchup.max_messages,
                        limit_mode="latest",
                        filter_command=True,
                        filter_mai=True,
                        include_binary_data=False,
                    )
                    if not isinstance(response, dict) or not response.get("success"):
                        raise ValueError("历史读取失败")
                    rows = response.get("messages") or []
                    filtered = await optional_api(p.ctx, "zeyr.mai-circle.filter_humans", messages=rows)
                    if filtered:
                        rows = filtered["messages"]
                    valid = [
                        row
                        for row in rows
                        if row.get("processed_plain_text")
                        and not str(row["processed_plain_text"]).startswith("/")
                        and not row.get("is_bot")
                        and not row.get("is_self")
                        and not (
                            p.own_user_id and identity(row)[0] == "qq" and identity(row)[1] == p.own_user_id
                        )
                    ]
                    count = len(valid)
                    self.status[stream] = {
                        "messages_read": count,
                        "truncated": len(response.get("messages") or []) >= p.config.catchup.max_messages,
                    }
                    if count >= p.config.catchup.min_messages:
                        material = json.dumps(
                            [
                                {"user": identity(row)[1], "text": row["processed_plain_text"]}
                                for row in valid
                            ],
                            ensure_ascii=False,
                        )[-p.config.catchup.max_chars :]
                        generated = await p.ctx.llm.generate(
                            render(p.config.catchup.instruction, material=material),
                            model=p.config.catchup.model_task,
                        )
                        if not isinstance(generated, dict) or not generated.get("success"):
                            raise ValueError("摘要失败")
                        text = str(generated.get("response") or generated.get("text") or "")[
                            : p.config.catchup.summary_chars
                        ]
                        if not text:
                            raise ValueError("摘要为空")
                        appended = await p.ctx.maisaka.context.append(
                            stream,
                            [{"type": "text", "data": text}],
                            visible_text=text,
                            source_kind="sleep_catchup",
                            replyable=False,
                        )
                        if not isinstance(appended, dict) or not appended.get("success"):
                            raise ValueError("插卡失败")
                    p.store.data["catchup_done"][key] = self.status[stream]
                    p.store.save()
            except asyncio.CancelledError:
                raise
            except Exception:
                failed = True
                p.ctx.logger.exception("转醒爬楼失败，保留待处理状态")
        p.store.data["catchup_pending"] = failed
        p.store.save()
