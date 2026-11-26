<div align="center">

# 麦的一天 · `zeyr.mai-day`

**给 Bot 一条有呼吸感的真实作息时间线：忙时少插话、被约懂推脱、睡着群聊零思考消耗、醒来超 99 条自动爬楼补课**

![Version](https://img.shields.io/badge/version-0.2.0-brightgreen)
![MaiBot](https://img.shields.io/badge/MaiBot-1.4.8%2B-2f81f7)
![SDK](https://img.shields.io/badge/SDK-2.23.0-0e8a16)
![Standalone](https://img.shields.io/badge/独立运行-支持-blue)

[为什么用它](#为什么用它) · [3 步快速开始](#3-步快速开始) · [配置项说明](#常用配置项说明) · [命令与控制台](#工具命令与控制台) · [FAQ](#常见疑问)

</div>

---

## 为什么用它

| 常见问题 | 传统作息插件的痛点 | **麦的一天（`zeyr.mai-day`）** |
|---|---|---|
| 半夜睡觉丢记忆或烧额度 | 入口直接拦截会导致半夜群聊记忆全漏；只拦发消息半夜仍不断触发 Planner 空烧思考 | 入睡自动把群聊（含观察群）频率压成 **`0.0` 零思考纯观望，记忆 100% 继续学习**；私聊与边缘窗口由护栏兜底 |
| 一觉醒来跟没睡过一样 | 早上一开口接不上群友半夜聊的话题，或根本不知刚聊了上百楼 | 醒来自动检查错过条数：**达到 99 条才调用轻量模型写爬楼笔记**并塞进上下文，低于 99 条省额度 |
| 别人约时间要么全答应要么全拒 | 所有时段一刀切，问起在干嘛每次靠模型现场编造 | 时间块区分 **`locked` / `soft` / `open`**：撞刚性课或约定建议推脱并给出空闲窗，软安排可商量挪动 |
| 聊嗨了瞬间被强行下线 | 一到 23:00 秒哑火，或因为跨过 00:00 逻辑清零 | 支持**聊嗨弹性延睡**（私聊可设更高粘性）+ **跨午夜逻辑作息日（默认 05:00 换日）**，昨晚熬夜今早自动多睡补觉 |

---

## 3 步快速开始

### WebUI 上手（推荐）
1. **重要提醒**：如果之前装过旧版 `maibot-community.mai-life`，先禁用旧插件；将 `mai-day/` 放入宿主 `plugins/` 并重启 MaiBot。
2. 打开 **麦的一天** 配置页，先调 **作息模板**（学生党 / 上班族 / 夜猫子 / 自定义）、**生活波动感**（推荐 `natural`）和 **睡眠时静默**（推荐开启）。
3. 如需屏蔽特定观察群或设置主动早晚安，点开高级设置填写 **纯观察静默群** 与 **早安/晚安方式**，并在顶部控件点 **刷新今日时间线图** 一眼确认今天的时间安排。

<details>
<summary>手改 config.toml 示例（附完整注释）</summary>

```toml
enabled = true
preset = "student"                                   # student 学生党 | worker 上班族 | night_owl 夜猫子 | custom
volatility_level = "natural"                         # fixed 铁律 | calm 微起伏 | natural 自然真人感 | chaotic 随性
sleep_silence = true                                 # 睡着后群聊频率归零（0思考消费），记忆学习 100% 继续
good_morning_mode = "passive_hint"                   # off | passive_hint 搭话顺嘴说 | proactive 主动向热聊目标发
good_night_mode = "passive_hint"

[catchup]
enabled = true
min_messages = 99                                    # 睡眠期间至少错过 99 条真人消息才调用模型整理爬楼速记

[weights]
enabled = true                                       # 清醒时自动合成「时段权重 × 群关系权重」（不依赖睡眠归零）

[[passive_observation_groups]]
platform = "qq"
group_id = "111222333"                               # 只观望学记忆、白天黑夜一律 0 倍率且绝不发言的群

[[admins]]
platform = "qq"
user_id = "987654"                                   # 留空亦可自动继承「群关系」里的管理员
```

</details>

---

## 常用配置项说明

| 配置项 | 默认值 | 怎么填与效果说明 |
|---|---|---|
| `preset` | `"worker"` | 四套内置双层周模板；含法定假日切周末、单独星期覆盖，可在高级区自定义每块细节 |
| `volatility_level` | `"natural"` | 四档波动（±0 / ±10 / ±25 / ±45 分钟），绝不推翻 `locked` 刚性块与 `hard_bedtime` |
| `sleep_silence` | `true` | 只要开启（不依赖 `weights.enabled`），入睡即把生效群与观察群倍率调成 `0.0`，醒来自动恢复原值 |
| `nap` | 默认关闭 | 可选午觉时间窗、概率、时长与 `debt_only`（仅昨晚熬夜欠觉时才午睡） |
| `catchup` | 门槛 `99` 条 | 过滤掉宿主自身及同行 Bot 后满 99 条才用轻量模型摘要，以 `replyable=False` 插卡回上下文 |
| `weights` | `enabled = true` | 清醒时段权重（`locked=0.35 / soft=0.85 / open=1.05`）× `mai-circle` 关系权重，停用或卸载还原原值 |
| `daily_polish` | 默认关闭 | 可选每天 05:00 换日后请轻量模型把枯燥活动标题润色一次，不改时间与刚度 |

---

## 工具、命令与控制台

### 对话工具与管理命令
| 类型 | 名称 / 语法 | 说明 |
|---|---|---|
| **对话工具** | `query_today_schedule(scope, start, end, event_text)` | 查今日底稿/有效安排/动态；带 `start/end` 检查档期冲突（`free` / `negotiable` / `conflict_locked`）；带 `event_text` 实时记变化 |
| **管理命令** | `/日程 + HH:MM[-HH:MM] <内容> [locked\|soft\|open]` | 管理员向今天追加手动日程并重算空档 |
| **管理命令** | `/唤醒` | 管理员在群里临时唤醒 Bot（默认清醒 15 分钟，自动放行命令回执） |

### WebUI 运行时控制台（`config_widgets`）
| 控件 | 说明 |
|---|---|
| `运行状态与生效范围` | 显示当前作息预设、波动感、当下阶段（清醒 / 睡觉静默中）、累积睡眠债与 `[scope]` |
| `爬楼与频率状态` | 查看各群已统计爬楼进度、向宿主下发的当前实时倍率，以及点击渲染的**今日后续时间线预览图** |
| `刷新今日时间线图` | 使用本地 `ctx.render.html2png` 无外网渲染今天剩余日程图片 |
| `临时唤醒` | 不在群里打字直接从后台临时唤醒 |
| `刷新今日未来软安排` | 保留今天已发生事件与 `locked` 约定，只对未来柔性空档重排投影（带二次确认） |

---

## 生态联动与常见疑问

- **全生态协调**：自动读取 `mai-calendar` 放假/调休与 `self/mutual` 待办、读取 `mai-profile` 作息偏好、合成 `mai-circle` 关系权重；为 `mai-insights` 提供 `open` 窗口共享租约；并给 `mai-calendar`、`mai-circle`、`mai-insights` 的命令/提醒/总结提供 **30 秒精确文本授权**，睡着期间绝不误杀指令回执。
- **Q：为什么私聊睡着时不会写频率 `0`？**
  MaiBot 宿主底层对私聊会忽略 `frequency.set_adjust()`，所以群聊走 `0.0` 倍率实现零 Planner 思考，私聊则由本插件的 `planner` 移工具 + `replyer` + `before_send` 护栏保底确保不发消息。
- **Q：会迁移或覆盖旧版 `mai-life` 数据吗？**
  完全不会。本插件不读取、不迁移也不删改旧 `mai-life` 的任何文件。

<details>
<summary>开发、门禁与安全审计</summary>

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r .github/requirements-ci.txt
.venv/bin/python scripts/check.py
```

- 包含 60 项边界、波动、爬楼、频率协调及睡眠静默解耦回归测试。

</details>
