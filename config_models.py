from datetime import time
from typing import Literal
from zoneinfo import ZoneInfo

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator, model_validator

Flexibility = Literal["locked", "soft", "open"]


class Block(PluginConfigBase):
    start: str = Field(default="09:00", description="开始 HH:MM")
    end: str = Field(default="10:00", description="结束 HH:MM；跨午夜自动续接")
    title: str = Field(default="自由安排", description="活动")
    flexibility: Flexibility = Field(default="open", description="刚度")
    sleeping: bool = Field(default=False, description="睡眠时间块")

    @field_validator("start", "end")
    @classmethod
    def valid_time(cls, value):
        time.fromisoformat(value)
        if len(value) != 5:
            raise ValueError("时间使用 HH:MM")
        return value

    @model_validator(mode="after")
    def distinct_times(self):
        if self.start == self.end:
            raise ValueError("开始与结束不能相同")
        return self


def blocks(*rows):
    return [Block(start=s, end=e, title=t, flexibility=f, sleeping=z) for s, e, t, f, z in rows]


class Week(PluginConfigBase):
    weekdays: list[Block] = Field(default_factory=list, description="周一至周五")
    weekend: list[Block] = Field(default_factory=list, description="周末和节假日")
    overrides: dict[str, list[Block]] = Field(default_factory=dict, description="指定星期覆盖，1 至 7")


def presets():
    free = blocks(
        ("08:00", "12:00", "自由安排", "open", False),
        ("12:00", "13:00", "午饭", "soft", False),
        ("13:00", "23:30", "自由安排", "open", False),
        ("23:30", "08:00", "睡觉", "soft", True),
    )
    worker = blocks(
        ("07:00", "09:00", "起床与早餐", "soft", False),
        ("09:00", "12:00", "工作", "locked", False),
        ("12:00", "13:00", "午饭", "soft", False),
        ("13:00", "18:00", "工作", "locked", False),
        ("18:00", "19:00", "晚饭", "soft", False),
        ("19:00", "23:00", "自由安排", "open", False),
        ("23:00", "07:00", "睡觉", "soft", True),
    )
    student = blocks(
        ("07:00", "08:00", "起床与早餐", "soft", False),
        ("08:00", "12:00", "课程", "locked", False),
        ("12:00", "14:00", "午饭与休息", "soft", False),
        ("14:00", "17:00", "课程", "locked", False),
        ("17:00", "19:00", "晚饭与自由安排", "open", False),
        ("19:00", "21:00", "学习", "soft", False),
        ("21:00", "23:30", "自由安排", "open", False),
        ("23:30", "07:00", "睡觉", "soft", True),
    )
    night = blocks(
        ("11:00", "13:00", "起床与午饭", "soft", False),
        ("13:00", "18:00", "工作学习", "soft", False),
        ("18:00", "19:00", "晚饭", "soft", False),
        ("19:00", "02:00", "自由安排", "open", False),
        ("02:00", "11:00", "睡觉", "soft", True),
    )
    return {
        "student": Week(weekdays=student, weekend=free),
        "worker": Week(weekdays=worker, weekend=free),
        "night_owl": Week(weekdays=night, weekend=night),
        "custom": Week(weekdays=free, weekend=free),
    }


class Volatility(PluginConfigBase):
    jitter_minutes: int = Field(default=0, ge=0, le=60, description="最大时间抖动")
    energy: bool = Field(default=False, description="启用精力节律")
    sleep_debt: bool = Field(default=False, description="跨日睡眠债补偿")
    micro_probability: float = Field(default=0, ge=0, le=1, description="软日程插曲概率")
    delay_sleep: bool = Field(default=False, description="参与聊天时弹性延睡")


def volatility():
    return {
        "fixed": Volatility(),
        "calm": Volatility(jitter_minutes=10, energy=True, micro_probability=0.10),
        "natural": Volatility(
            jitter_minutes=25, energy=True, sleep_debt=True, micro_probability=0.25, delay_sleep=True
        ),
        "chaotic": Volatility(
            jitter_minutes=45, energy=True, sleep_debt=True, micro_probability=0.45, delay_sleep=True
        ),
    }


class Target(PluginConfigBase):
    platform: str = Field(default="qq", description="平台")
    group_id: str = Field(default="", description="群标识；与 user_id 二选一")
    user_id: str = Field(default="", description="用户标识")


class Extra(PluginConfigBase):
    date: str = Field(default="", description="日期 YYYY-MM-DD；空值为每日")
    block: Block = Field(default_factory=Block)


class PluginInfo(PluginConfigBase):
    config_version: str = Field(default="0.1.0", description="配置版本，由 Runner 管理")


class Config(PluginConfigBase):
    show_advanced: bool = Field(default=False, description="显示高级设置")
    plugin: PluginInfo = Field(default_factory=PluginInfo)
    enabled: bool = Field(default=True, description="启用生活状态")
    preset: Literal["student", "worker", "night_owl", "custom"] = Field(
        default="worker", description="生活预设"
    )
    volatility_level: Literal["fixed", "calm", "natural", "chaotic"] = Field(
        default="natural", description="波动等级"
    )
    sleep_silence: bool = Field(default=True, description="睡眠时静默，接收与学习继续")
    good_morning_mode: Literal["off", "passive_hint", "proactive"] = Field(
        default="passive_hint", description="早安方式"
    )
    good_night_mode: Literal["off", "passive_hint", "proactive"] = Field(
        default="passive_hint", description="晚安方式"
    )
    timezone: str = Field(default="Asia/Shanghai", description="时区")
    day_rollover_hour: int = Field(default=5, ge=0, le=23, description="逻辑日底稿生成参考小时")
    window_days: int = Field(default=3, ge=1, le=3, description="连续状态工作窗口天数")
    hard_bedtime: str = Field(default="03:00", description="最晚入睡时间")
    forced_rest_hours: int = Field(default=8, ge=1, le=12, description="连续清醒到上限后的休息时长")
    micro_duration_minutes: int = Field(default=20, ge=1, le=60, description="小插曲最长时长")
    max_awake_hours: int = Field(default=24, ge=8, le=72, description="连续清醒硬上限")
    delay_minutes: int = Field(default=15, ge=1, le=60, description="参与聊天后延睡分钟")
    engaged_minutes: int = Field(default=10, ge=1, le=60, description="近期 Bot 发过言才视为参与聊天")
    max_debt_minutes: int = Field(default=120, ge=0, le=360, description="最大睡眠债")
    compensation_minutes: int = Field(default=30, ge=0, le=120, description="单日补眠上限")
    wake_minutes: int = Field(default=15, ge=1, le=120, description="管理员临时唤醒时长")
    greeting_window_minutes: int = Field(default=30, ge=1, le=120, description="状态问候有效窗口")
    active_group_recent_minutes: int = Field(
        default=120, ge=1, le=1440, description="主动问候所需真人活跃窗口"
    )
    share_cooldown_minutes: int = Field(default=60, ge=1, le=1440, description="主动分享共享冷却")
    share_lease_seconds: int = Field(default=120, ge=10, le=600, description="发送租约有效期")
    tick_seconds: int = Field(default=30, ge=5, le=300, description="后台调度间隔")
    hint_chars: int = Field(default=25, ge=0, le=25, description="当下锚点最大字数")
    presets: dict[str, Week] = Field(default_factory=presets, description="两层周模板")
    volatility: dict[str, Volatility] = Field(default_factory=volatility, description="四档参数")
    micro_events: list[str] = Field(
        default_factory=lambda: ["取快递", "下楼散步", "买杯饮料"], description="小插曲池"
    )
    extras: list[Extra] = Field(default_factory=list, description="手动额外日程")
    admins: list[Target] = Field(default_factory=list, description="管理命令鉴权；不进入 Prompt")
    targets: list[Target] = Field(default_factory=list, description="主动问候目标")
    passive_observation_groups: list[Target] = Field(
        default_factory=list, description="只采集、不主动发送的群"
    )
    anchor_template: str = Field(default="{title}{status}", description="当下状态模板")
    open_title: str = Field(default="自由安排", description="未占用时间的名称")
    sleepy_hint: str = Field(default="，准备睡了", description="临睡提示")
    waking_hint: str = Field(default="，刚醒", description="醒来提示")
    sleep_hint: str = Field(default="，休息中", description="睡眠提示")
    low_energy_hint: str = Field(default="，有点困", description="午后精力提示")
    greeting_instruction: str = Field(
        default="根据材料，用当前会话的人设自然说一句{kind}，不要列清单。", description="问候生成要求"
    )
    event_template: str = Field(default="{kind}：{detail}", description="动态事件渲染")

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator("hard_bedtime")
    @classmethod
    def valid_bedtime(cls, value):
        time.fromisoformat(value)
        if len(value) != 5:
            raise ValueError("时间使用 HH:MM")
        return value

    @model_validator(mode="after")
    def selected_presets_exist(self):
        if self.preset not in self.presets or self.volatility_level not in self.volatility:
            raise ValueError("选中的日程或波动预设不存在")
        return self


# SDK 2.23 supports conditional fields; keep the first-use form small.
_BASIC_FIELDS = ["enabled", "preset", "show_advanced", "sleep_silence", "volatility_level"]
for _name, _field in Config.model_fields.items():
    if _name not in _BASIC_FIELDS and _name != "plugin":
        _field.json_schema_extra = {
            **(_field.json_schema_extra or {}),
            "depends_on": "show_advanced",
            "depends_value": True,
        }
Config.model_rebuild(force=True)
