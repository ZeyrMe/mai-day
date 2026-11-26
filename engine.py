"""Deterministic daily plans plus append-only events; no Host imports or model dependencies."""

from __future__ import annotations

import hashlib
import random
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config_models import Block, Config
from .runtime import StateFile, render


def parse(value):
    return datetime.fromisoformat(value)


class Engine:
    def __init__(self, config: Config, store: StateFile):
        self.config, self.store = config, store
        self.zone = ZoneInfo(config.timezone)

    def now(self):
        return datetime.now(self.zone)

    def logical_date(self, now):
        local = now.astimezone(self.zone)
        return (local - timedelta(hours=self.config.day_rollover_hour)).date()

    def wall(self, day: date, hhmm: str):
        value = datetime.combine(day, time.fromisoformat(hhmm), self.zone)
        if value.hour < self.config.day_rollover_hour:
            value += timedelta(days=1)
        return value

    def event(self, kind, detail, now, *, source="day", event_id="", **fields):
        key = event_id or uuid.uuid4().hex
        for item in self.store.data["events"]:
            if item["id"] == key:
                return item
        row = {
            "id": key,
            "at": now.isoformat(),
            "logical_date": self.logical_date(now).isoformat(),
            "kind": kind,
            "text": render(self.config.event_template, kind=kind, detail=detail),
            "source": source,
            **fields,
        }
        self.store.data["events"].append(row)
        self.store.save()
        return row

    def block_row(self, day, block, key):
        start, end = self.wall(day, block.start), self.wall(day, block.end)
        if end <= start:
            end += timedelta(days=1)
        return {
            "id": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "title": block.title,
            "flexibility": block.flexibility,
            "sleeping": block.sleeping,
        }

    def plan(self, day: date, context=None, todos=None):
        key = day.isoformat()
        data = self.store.data
        if key in data["plans"]:
            return data["plans"][key]
        week = self.config.presets[self.config.preset]
        weekend = (context or {}).get("is_workday") is False
        if (context or {}).get("is_workday") is None:
            weekend = day.weekday() >= 5
        skeleton = week.overrides.get(str(day.isoweekday()), week.weekend if weekend else week.weekdays)
        rows = [self.block_row(day, block, f"{key}:{i}") for i, block in enumerate(skeleton)]
        rows += [
            self.block_row(day, extra.block, f"{key}:extra:{i}")
            for i, extra in enumerate(self.config.extras)
            if not extra.date or extra.date == key
        ]
        for todo in todos or []:
            if todo.get("ownership") not in ("self", "mutual"):
                continue
            try:
                start, end = parse(todo["start"]), parse(todo["end"])
                if start.tzinfo is None or end.tzinfo is None or end <= start:
                    continue
                rows.append(
                    {
                        "id": f"todo:{todo['id']}",
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "title": str(todo["text"]),
                        "flexibility": "locked",
                        "sleeping": False,
                    }
                )
            except (KeyError, ValueError, TypeError):
                continue
        settings = self.config.volatility[self.config.volatility_level]
        seed = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
        rng = random.Random(seed)
        locked = [row for row in rows if row["flexibility"] == "locked"]
        conflicts = []
        for index, row in enumerate(locked):
            for other in locked[index + 1 :]:
                if parse(row["start"]) < parse(other["end"]) and parse(other["start"]) < parse(row["end"]):
                    conflicts.append([row["id"], other["id"]])
        changes = []
        for row in rows:
            if row["flexibility"] == "locked":
                continue
            delta = rng.randint(-settings.jitter_minutes, settings.jitter_minutes)
            start, end = (
                parse(row["start"]) + timedelta(minutes=delta),
                parse(row["end"]) + timedelta(minutes=delta),
            )
            if row["sleeping"]:
                start = min(start, self.wall(day, self.config.hard_bedtime))
            for anchor in locked:
                a, b = parse(anchor["start"]), parse(anchor["end"])
                if parse(row["start"]) >= b:
                    start = max(start, b)
                if parse(row["end"]) <= a:
                    end = min(end, a)
            if end <= start:
                start, end, delta = parse(row["start"]), parse(row["end"]), 0
            row["start"], row["end"] = start.isoformat(), end.isoformat()
            if delta:
                changes.append({"block_id": row["id"], "jitter_minutes": delta})
            if not row["sleeping"] and self.config.micro_events and rng.random() < settings.micro_probability:
                changes.append(
                    {
                        "block_id": row["id"],
                        "original_title": row["title"],
                        "title": rng.choice(self.config.micro_events),
                        "duration_minutes": min(
                            self.config.micro_duration_minutes, int((end - start).total_seconds() / 60)
                        ),
                    }
                )
        plan = {
            "logical_date": key,
            "generated_at": self.now().isoformat(),
            "preset": self.config.preset,
            "volatility_level": self.config.volatility_level,
            "blocks": sorted(rows, key=lambda row: row["start"]),
            "sampled_changes": changes,
            "conflicts": conflicts,
            "calendar": context or {},
        }
        data["plans"][key] = plan
        self.store.save()
        return plan

    def effective_blocks(self, now):
        day = self.logical_date(now)
        rows = []
        for offset in range(-(self.config.window_days - 1), 1):
            plan = self.plan(day + timedelta(days=offset))
            rows.extend({**row} for row in plan["blocks"])
        rows.extend(
            dict(row) for row in self.store.data["manual"] if parse(row["end"]) > now - timedelta(days=3)
        )
        for plan in self.store.data["plans"].values():
            for change in plan.get("sampled_changes", []):
                if "duration_minutes" not in change:
                    continue
                original = next((row for row in rows if row["id"] == change["block_id"]), None)
                if original:
                    deviation = dict(original)
                    deviation["id"] += ":micro"
                    deviation["title"] = change["title"]
                    deviation["end"] = min(
                        parse(original["end"]),
                        parse(original["start"]) + timedelta(minutes=change["duration_minutes"]),
                    ).isoformat()
                    deviation["manual"] = True
                    deviation["sleeping"] = False
                    rows.append(deviation)
        # Delays and compensation are overlays, never mutations of the original plan.
        for row in rows:
            overlay = self.store.data["overlays"].get(row["id"], {})
            row.update(overlay)
        # Produce a non-overlapping projection; immutable source plans remain available separately.
        # Higher-priority locked appointments and explicit additions take time from soft/open slots.
        ordered = sorted(
            rows,
            key=lambda row: (
                row["flexibility"] == "locked",
                row.get("manual", False),
                row["sleeping"],
                row["start"],
            ),
            reverse=True,
        )
        projected, occupied = [], []
        for row in ordered:
            segments = [(parse(row["start"]), parse(row["end"]))]
            for left, right in occupied:
                remaining = []
                for start, end in segments:
                    if right <= start or left >= end:
                        remaining.append((start, end))
                    else:
                        if start < left:
                            remaining.append((start, left))
                        if right < end:
                            remaining.append((right, end))
                segments = remaining
            for start, end in segments:
                if end > start:
                    projected.append({**row, "start": start.isoformat(), "end": end.isoformat()})
                    occupied.append((start, end))
        return sorted(projected, key=lambda row: row["start"])

    def state(self, now=None):
        now = (now or self.now()).astimezone(self.zone)
        if not self.config.enabled:
            return {
                "now": now.isoformat(),
                "calendar_date": now.date().isoformat(),
                "logical_date": self.logical_date(now).isoformat(),
                "sleeping": False,
                "phase": "disabled",
                "current_block": None,
                "anchor": "",
                "revision": len(self.store.data["events"]),
            }
        rows = self.effective_blocks(now)
        active = [row for row in rows if parse(row["start"]) <= now < parse(row["end"])]
        # Real appointments and manual extras take precedence; soft overlaps never override anchors.
        active.sort(
            key=lambda row: (
                row["flexibility"] == "locked",
                row.get("manual", False),
                row["sleeping"],
                row["start"],
            ),
            reverse=True,
        )
        current = (
            active[0]
            if active
            else {"id": "open", "title": self.config.open_title, "flexibility": "open", "sleeping": False}
        )
        awake_since = parse(self.store.data.get("awake_since") or now.isoformat())
        forced_sleep = (
            self.config.max_awake_hours * 3600
            <= (now - awake_since).total_seconds()
            < (self.config.max_awake_hours + self.config.forced_rest_hours) * 3600
        )
        wake_until = parse(self.store.data.get("wake_until") or now.isoformat())
        sleeping = (
            bool(current["sleeping"]) or (forced_sleep and current["flexibility"] != "locked")
        ) and not wake_until > now
        status = ""
        if sleeping:
            status = self.config.sleep_hint
        elif self.config.good_morning_mode == "passive_hint" and now - awake_since <= timedelta(
            minutes=self.config.greeting_window_minutes
        ):
            status = self.config.waking_hint
        else:
            impending = [
                row
                for row in rows
                if row["sleeping"]
                and now < parse(row["start"]) <= now + timedelta(minutes=self.config.greeting_window_minutes)
            ]
            if impending and self.config.good_night_mode == "passive_hint":
                status = self.config.sleepy_hint
            elif self.config.volatility[self.config.volatility_level].energy and 13 <= now.hour < 15:
                status = self.config.low_energy_hint
        return {
            "now": now.isoformat(),
            "calendar_date": now.date().isoformat(),
            "logical_date": self.logical_date(now).isoformat(),
            "sleeping": sleeping,
            "phase": "sleep" if sleeping else "awake",
            "current_block": current,
            "anchor": render(self.config.anchor_template, title=current["title"], status=status)[
                : self.config.hint_chars
            ],
            "revision": len(self.store.data["events"]),
            "sleep_debt_minutes": self.store.data["sleep_debt_minutes"],
        }

    def transition(self, now=None):
        now = now or self.now()
        state = self.state(now)
        previous = self.store.data.get("last_state")
        if not self.config.enabled:
            return state, previous
        if not self.store.data.get("awake_since"):
            self.store.data["awake_since"] = now.isoformat()
        if previous and previous["sleeping"] and not state["sleeping"]:
            self.store.data["awake_since"] = now.isoformat()
            overlay = self.store.data["overlays"].get(previous.get("block_id"), {})
            compensation = overlay.get("compensation_minutes", 0)
            if compensation and not overlay.get("compensated"):
                self.store.data["sleep_debt_minutes"] = max(
                    0, self.store.data["sleep_debt_minutes"] - compensation
                )
                overlay["compensated"] = True
                self.event("sleep_compensated", str(compensation), now)
            self.event("wake", state["anchor"], now, event_id=f"wake:{now.isoformat()}")
        elif previous and not previous["sleeping"] and state["sleeping"]:
            self.event("sleep", state["anchor"], now, event_id=f"sleep:{now.isoformat()}")
        if previous and previous.get("block_id") != state["current_block"]["id"]:
            self.event(
                "block_end",
                previous.get("title", ""),
                now,
                event_id=f"end:{previous.get('block_id')}:{previous.get('start')}",
            )
        self.store.data["last_state"] = {
            "sleeping": state["sleeping"],
            "block_id": state["current_block"]["id"],
            "title": state["current_block"]["title"],
            "start": state["current_block"].get("start", ""),
        }
        self.store.save()
        return state, previous

    def defer_sleep(self, now):
        settings = self.config.volatility[self.config.volatility_level]
        if not settings.delay_sleep or self.state(now)["sleeping"]:
            return False
        rows = self.effective_blocks(now)
        candidates = [
            row
            for row in rows
            if row["sleeping"]
            and row["flexibility"] != "locked"
            and now - timedelta(minutes=self.config.delay_minutes)
            <= parse(row["start"])
            <= now + timedelta(minutes=self.config.delay_minutes)
        ]
        if not candidates:
            return False
        row = min(candidates, key=lambda row: row["start"])
        original = next(
            item
            for plan in self.store.data["plans"].values()
            for item in plan["blocks"]
            if item["id"] == row["id"]
        )
        hard = self.wall(date.fromisoformat(row["id"][:10]), self.config.hard_bedtime)
        until = min(now + timedelta(minutes=self.config.delay_minutes), hard, parse(row["end"]))
        # A locked upcoming appointment always limits the deferral.
        for anchor in rows:
            if anchor["flexibility"] == "locked" and now < parse(anchor["start"]) < until:
                until = parse(anchor["start"])
        if until <= parse(row["start"]):
            return False
        shift = (until - parse(row["start"])).total_seconds() / 60
        overlay = self.store.data["overlays"].setdefault(row["id"], {})
        overlay["start"] = until.isoformat()
        if settings.sleep_debt:
            self.store.data["sleep_debt_minutes"] = min(
                self.config.max_debt_minutes, self.store.data["sleep_debt_minutes"] + shift
            )
            original_end = parse(original["end"])
            compensated_end = original_end + timedelta(
                minutes=min(self.config.compensation_minutes, self.store.data["sleep_debt_minutes"])
            )
            for anchor in rows:
                if anchor["flexibility"] == "locked" and original_end <= parse(anchor["start"]):
                    compensated_end = min(compensated_end, parse(anchor["start"]))
            overlay["end"] = compensated_end.isoformat()
            overlay["compensation_minutes"] = max(0, (compensated_end - original_end).total_seconds() / 60)
        self.event(
            "sleep_deferred",
            f"{row['start']} → {until.isoformat()}",
            now,
            event_id=f"delay:{row['id']}:{until.isoformat()}",
            block_id=row["id"],
            original_start=original["start"],
            effective_start=until.isoformat(),
        )
        return True

    def add_manual(self, block: Block, now, day=None):
        row = self.block_row(day or self.logical_date(now), block, f"manual:{uuid.uuid4().hex}")
        if parse(row["end"]) <= now:
            raise ValueError("日程结束时间已经过去")
        for other in self.effective_blocks(now):
            if (
                other["flexibility"] == "locked"
                and parse(row["start"]) < parse(other["end"])
                and parse(other["start"]) < parse(row["end"])
            ):
                raise ValueError(f"与刚性日程冲突：{other['title']}")
        row["manual"] = True
        self.store.data["manual"].append(row)
        self.event("manual_added", row["title"], now, event_id=row["id"], block=row)
        return row

    def free(self, stream_id, now=None, *, ignore_lease=False):
        now = now or self.now()
        if not stream_id:
            return False, "missing_stream"
        state = self.state(now)
        route = self.store.data["routes"].get(stream_id, {})
        observed = any(
            target.platform == route.get("platform")
            and target.group_id
            and target.group_id == route.get("group_id")
            for target in self.config.passive_observation_groups
        )
        if observed:
            return False, "observation_only"
        if not self.config.enabled:
            return True, "disabled"
        if state["sleeping"] or state["current_block"]["flexibility"] != "open":
            return False, "busy"
        last = self.store.data["shared_at"].get(stream_id)
        if last and now < parse(last) + timedelta(minutes=self.config.share_cooldown_minutes):
            return False, "cooldown"
        if not ignore_lease and any(
            lease["stream_id"] == stream_id and parse(lease["expires_at"]) > now
            for lease in self.store.data["leases"].values()
        ):
            return False, "reserved"
        return True, "open"

    def reserve(self, stream_id, now=None, *, purpose="share"):
        now = now or self.now()
        allowed, reason = self.free(stream_id, now)
        if purpose == "share" and not allowed:
            return {"value": False, "reason": reason}
        key = uuid.uuid4().hex
        lease = {
            "id": key,
            "stream_id": stream_id,
            "purpose": purpose,
            "expires_at": (now + timedelta(seconds=self.config.share_lease_seconds)).isoformat(),
        }
        self.store.data["leases"][key] = lease
        self.store.save()
        return {"value": True, "lease": lease}

    def settle(self, lease_id, sent, receipt_id, now=None):
        now = now or self.now()
        if not isinstance(sent, bool):
            raise ValueError("sent 必须是布尔值")
        if lease_id in self.store.data["settlements"]:
            return self.store.data["settlements"][lease_id]
        if sent and not receipt_id:
            raise ValueError("成功结算必须有回执标识")
        lease = self.store.data["leases"].pop(lease_id, None)
        if lease is None:
            raise ValueError("租约不存在")
        # A valid send may finish shortly after the lease expires: actual receipt is the source of truth.
        row = {"sent": bool(sent), "receipt_id": receipt_id, "lease_id": lease_id}
        self.store.data["settlements"][lease_id] = row
        if sent:
            self.store.data["shared_at"][lease["stream_id"]] = now.isoformat()
            self.event(
                "shared",
                lease["purpose"],
                now,
                event_id=f"{lease['stream_id']}:receipt:{receipt_id}",
                source=lease["purpose"],
            )
        self.store.save()
        return row


def initial_state():
    return {
        "schema_version": 1,
        "plans": {},
        "events": [],
        "manual": [],
        "overlays": {},
        "routes": {},
        "sleep_debt_minutes": 0,
        "awake_since": "",
        "wake_until": "",
        "last_state": None,
        "leases": {},
        "settlements": {},
        "shared_at": {},
        "greetings": {},
    }
