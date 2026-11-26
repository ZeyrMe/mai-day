# 麦的一天 · `zeyr.mai-day`

> 两层周模板、块级刚度与档期协商、四级拟真作息波动、睡眠时段“频率归零零思考纯观望”、醒来 99 条爬楼速记，以及全生态节拍协调中心。

![Version](https://img.shields.io/badge/version-0.2.0-green)
![MaiBot](https://img.shields.io/badge/MaiBot-1.4.8%2B-blue)
![SDK](https://img.shields.io/badge/SDK-2.23.0-teal)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

## 核心特性

- **两层作息 + 三级刚度 + 块级细调**：
  - 内置 `student` / `worker` / `night_owl` / `custom` 预设（工作日 / 周末假日 / 指定星期覆盖），支持跨午夜逻辑作息日（默认 `05:00` 换日，重启不断点）；
  - 每个时间块区分 `locked`（刚性课/工作/双方约定）、`soft`（吃饭/睡眠，可抖动与商量）和 `open`（自由摸鱼槽），且支持块级独立 `max_shift_minutes`（`-1` 继承波动档位，`0` 不随机偏移）与 `negotiable`。
- **同意 / 推脱 / 可商量的档期闭环**：对话中被约时间时，`query_today_schedule` 与 `check_availability` 直接返回 `conflict_locked`（附今日最近空闲窗方便礼貌推脱）、`negotiable`（说明可挪范围）或 `free`，答应后还能顺手记入动态事件流（底稿保持不可变，动态以自然中文只增不覆）。
- **四级波动 + 可选午觉 + 每日轻润色**：提供 `fixed / calm / natural / chaotic` 四档波动、聊嗨延迟入睡（含私聊更粘人倍率 + 最晚死线 `hard_bedtime`）与次日睡眠债补觉；可选开启条件午觉（`nap`）和每天 1 次轻量活动标题润色（`daily_polish`）。
- **夜间真·零思考不丢消息**：
  - 只要启用 `sleep_silence`（即使关闭白天动态调权），入睡后自动把群聊频率倍率压至 `0.0`（包括观察群与冷启动首条睡着群消息），**走宿主原生静默消费（0 次 Planner 调用，100% 记忆/表达/黑话入库学习）**，醒来自动精准恢复入睡前原倍率；
  - 私聊（宿主底层忽略 `set_adjust`）与边缘窗口由 `planner` 移工具 + `replyer` + `before_send` 护栏稳妥兜底，并严格放行其他插件带 30 秒精确授权的命令回执、到期提醒与工具总结。
- **醒来“爬楼补课感”**：睡醒按每个会话错过消息量决定是否拉模型——低于 `99` 条不浪费模型直接看近期记录；达到 `99` 条则读取最新有界历史（过滤宿主自身与同行 Bot）生成简短速记，以 `replyable=False` 插卡放回会话上下文。
- **时段 × 关系频率合成 & 早晚安**：清醒时按当前时段权重与 `mai-circle` 关系权重乘法合成群聊发言倍率（无 `mai-day` 时由 `mai-circle` 独立接手）；支持 `off` / `passive_hint` / `proactive` 三档早晚安（主动模式仅对近期热聊过的活跃群或显式私聊对象，靠人设实时生成且绝不在午觉时误发）。

---

## 安装与快速配置

将 `mai-day/` 放入宿主 `plugins/` 目录并重启 MaiBot；建议首次上线直接先调 3 项（作息模板、波动档位、是否启用睡眠静默）即可生效。

`config.toml` 示例：

```toml
enabled = true
preset = "student"                 # student | worker | night_owl | custom
volatility_level = "natural"       # fixed | calm | natural | chaotic
sleep_silence = true
good_morning_mode = "passive_hint" # off | passive_hint | proactive
good_night_mode = "passive_hint"

[[passive_observation_groups]]
platform = "qq"
group_id = "111222333"

[[targets]]
platform = "qq"
group_id = "999888777"

[[admins]]
platform = "qq"
user_id = "987654"
```

---

## 工具、命令、接口与 WebUI 控制台

### 对话工具（`@Tool`）
| 工具名 | 参数 | 作用 |
|---|---|---|
| `query_today_schedule` | `scope="all"`, `start=""`, `end=""`, `event_text=""` | 查询当天底稿/当前生效日程/近期动态；传入 `start/end` 检查档期冲突；传入 `event_text` 实时记一笔生活变化 |

### 管理命令（需在 `admins` 或 `mai-circle.admins` 名单内）
| 命令格式 | 效果 |
|---|---|
| `/日程 + HH:MM[-HH:MM] <内容> [locked\|soft\|open]` | 向当天追加手动日程块并即时重算冲突与未来空档 |
| `/唤醒` | 临时唤醒 Bot（默认持续 `15` 分钟） |

### 对外联动接口（`@API(..., version="1")`）
- `get_state(stream_id="")` / `check_availability(start, end="")` / `append_dynamic_event(text, source, event_id)`
- `is_free_for_sharing(stream_id)` / `reserve_share(stream_id)` / `authorize_share(lease_id, text)` / `settle_share(lease_id, sent, receipt_id)`
- `authorize_service_send(stream_id, text)` / `revoke_service_send(stream_id, text)`

### WebUI 运行时控制台（`config_widgets`）
| 控件 | 类型 | 说明 |
|---|---|---|
| `运行状态与生效范围` | 实时状态 | 展示当前预设、波动等级、实时时间块/睡眠债与 `[scope]` 状态 |
| `爬楼与频率状态` | 状态 / 图表 | 展示各会话最近爬楼统计、向宿主写入的当前频率倍率，以及渲染后的今日后续时间线图片 |
| `刷新今日时间线图` | 一键按钮 | 使用本地 `ctx.render.html2png` 无外网渲染今日后续时间线预览图 |
| `临时唤醒` | 一键按钮 | 后台立即临时唤醒当前状态，无需在群里发命令 |
| `刷新今日未来软安排` | 二次确认按钮 | 保留已发生事件与 `locked` 约定，重新投影今天剩余的软安排 |
| `刷新公共集合缓存` | 一键按钮 | 刷新 `@set:` / `@tag:` 范围缓存 |

---

## 生态联动与注意事项

- **与全生态联动**：自动读取 `mai-calendar` 当天节日/调休与 `self`/`mutual` 待办、读取 `mai-profile` 作息偏好、读取 `mai-circle` 关系权重与 Bot 列表，并为 `mai-insights` 提供分享租约与回执冷却统一协调。
- **不迁移旧版 `mai-life`**：若之前装过旧版 `maibot-community.mai-life`，请在运行前将其禁用；本插件使用独立的 `life.json`，绝不修改或删除旧数据。

<details>
<summary>开发、门禁与安全审计（独立仓库维护）</summary>

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r .github/requirements-ci.txt
.venv/bin/python scripts/check.py
```

- 包含 60 项本地单元/边界/频率协调测试与端到端睡眠门控校验，以及 CI Ruff/审计/密钥扫描流水线。

</details>
