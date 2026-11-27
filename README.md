# 麦的一天（mai-day）

为麦麦提供日常作息、日程安排与夜间安静控制

![Version](https://img.shields.io/badge/version-0.2.0-brightgreen)
![MaiBot](https://img.shields.io/badge/MaiBot-1.4.8%2B-2f81f7)
![SDK](https://img.shields.io/badge/SDK-2.23.0-0e8a16)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776ab)
![Standalone](https://img.shields.io/badge/独立运行-支持-blue)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

## 功能介绍

- **灵活的日常作息**：内置学生党、上班族、夜猫子与自定义 4 套周模板；日程块区分固定（`locked`）、常规（`soft`）与空闲（`open`），支持按波动档位产生自然的早晚浮动与午睡。
- **夜间安静不丢记忆**：入睡时将群聊发言频率调至 `0.0`，不停止接收消息和学习记忆，同时避免夜间无意义触发对话思考；私聊则通过发送护栏保持安静。
- **睡醒消息回顾**：睡眠期间若群内有效真人消息超过阈值（默认 99 条），醒来后自动用轻量模型梳理错过的话题写入上下文。
- **弹性延睡与冲突检查**：快到睡点仍在热聊时可适当延后入睡并在次日补觉；被邀约时能根据固定日程判断是否需要协商改期。

## 效果示例

- **夜间安静不丢记忆**：进入睡眠时段后群聊频率降为 `0.0`，后台不触发思考消耗额度，但群消息和记忆照常收录；醒来时若期间错过达到 99 条真人讨论，会自动补一份简短回顾。
- **有事少说、约时间看档期**：上班或上课（`locked`）时段发言频率自动降低，被约在冲突时间时会根据 `check_availability` 返回建议改期或给出空闲时段。

## 如何使用

1. 若之前装过旧版 `mai-life`，先将其禁用；将 `mai-day/` 放入宿主 `plugins/` 目录并重启。
2. 打开 **麦的一天** 配置页，选择 **作息模板**、**作息波动**（推荐 `natural`）并保持 **夜间安静** 开启。
3. 如需设置一直静音学习的观察群或主动早晚安对象，展开高级设置填写 **纯观察静默群** 与 **主动问候目标**；点击顶部 **刷新今日时间线图** 可预览今日安排。

<details>
<summary>config.toml 配置示例</summary>

```toml
enabled = true
preset = "student"                                   # student | worker | night_owl | custom
volatility_level = "natural"                         # fixed | calm | natural | chaotic
sleep_silence = true
good_morning_mode = "passive_hint"                   # off | passive_hint | proactive
good_night_mode = "passive_hint"

[catchup]
enabled = true
min_messages = 99

[weights]
enabled = true

[[passive_observation_groups]]
platform = "qq"
group_id = "111222333"

[[admins]]
platform = "qq"
user_id = "987654"
```

</details>

## 配置说明

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `preset` | `"worker"` | 周作息模板，节假日与补班日会自动联动 `mai-calendar` 切换 |
| `volatility_level` | `"natural"` | 四档日常波动设置，不会改变 `locked` 固定日程与最晚入睡时间 |
| `sleep_silence` | `true` | 夜间安静开关：入睡后把群聊频率调成 `0.0`，醒来自动恢复原值 |
| `nap` | 默认关闭 | 高级设置：午睡时间窗口、概率、时长与是否仅欠觉时触发 |
| `catchup` | 门槛 `99` | 高级设置：睡醒消息回顾配置，过滤其他 Bot 后达标才触发摘要 |
| `weights` | `enabled = true` | 高级设置：清醒时段根据日程忙闲与 `mai-circle` 关系调节群聊频率 |
| `daily_polish` | 默认关闭 | 高级设置：每天换日后用轻量模型润色常规活动名称 |

## 命令与工具

| 类别 | 名称 / 格式 | 说明 |
|---|---|---|
| 对话工具 | `query_today_schedule(scope, start, end, event_text)` | 查询今日安排与当前状态、检查时间段是否有空，或记录临时日程变化 |
| 管理命令 | `/日程 + HH:MM[-HH:MM] <内容> [locked\|soft\|open]` | 向今天追加一条临时日程并重算空档（需管理员） |
| 管理命令 | `/唤醒` | 临时唤醒 Bot 一段时间（默认 15 分钟，需管理员） |
| WebUI 控件 | `刷新今日时间线图` / `临时唤醒` / `刷新今日未来软安排` | 渲染当天剩余日程图片、手动唤醒或重排未来非固定时段 |

<details>
<summary>开发与自检</summary>

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r .github/requirements-ci.txt
.venv/bin/python scripts/check.py
```

</details>
