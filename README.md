# 麦的一天

`zeyr.mai-day` · 日程、连续动态与不丢消息睡眠。

开发基线 MaiBot 1.4.8 / SDK 2.23.0。可独立安装，其他 zeyr 插件仅通过公开 API 自动联动。

首批已实现：student/worker/night_owl/custom 周模板、locked/soft/open 三级刚度、四档波动、三日连续状态窗口、每日底稿与只增动态流、睡眠债和聊嗨延睡、手动 `/日程 + HH:MM ... [locked|soft|open]`、`/唤醒`、`get_state`/分享租约 API，以及睡眠时只静默回复和发送、持续接收入库学习的双保险。主动问候采用独立 Replyer 生成后按实际发送回执结算；观察群始终只采集。

分享流程是 `reserve_share` → `authorize_share`（精确文本短期授权）→ 实际发送 → `settle_share`，发送前会再次检查窗口。没有 calendar/circle/insights 时仍可独立运行，并使用本地配置或降级路径。

已在 MaiBot 1.4.8 / SDK 2.23.0 Docker 宿主完成安装和启动检查。睡眠期服务发送采用 30 秒、会话与精确文本绑定的一次性授权；普通聊天仍受睡眠门控，观察群优先禁发。入站活跃时间在内存更新，由后台 tick 和卸载统一持久化。真实目标群发送仍需结合实例配置验收。

安装时将整个目录同步到宿主 plugins；运行 config.toml 由 SDK 生成。实例名单、个人资料和密钥只填运行配置，不提交。

检查：`python -m pytest`，开发依赖见 pyproject.toml。


## v2.5 第一批：范围与原生控制台

新增独立 `[scope]`：mode 默认 all；groups / privates / users 接受 `group:标识` / `private:标识` / `user:标识`，可带平台前缀（默认 qq）。mode=whitelist 只允许命中对象；mode=blacklist 排除命中对象；all 允许所有对象。exclude 始终优先。allow_private=false 仅放行名单明确指定的私聊，主动私聊仍需单独配置目标。

可用 `@set:名称` / `@tag:名称` 复用 mai-circle 集合/标签；缺席时按空集合降级，不影响直接对象。受限模式在没有真实会话身份时跳过业务，消息仍正常进入宿主。重启后的会话身份缓存由入站消息恢复。

WebUI 配置页提供运行状态面板与“刷新公共集合缓存”按钮，不调用模型、不发送聊天。范围分组默认折叠；字段采用中文标签与结构化条目卡片。

presets 改为带 name 的条目列表，volatility 改为 volatility_profiles，星期 overrides 改为 weekday / blocks 列表；旧结构自动转换。

本批只升级公共基础设施，完整 v2.5 的业务能力尚未全部实现。旧配置保持可读，Runner 规范化写回后采用新结构；回退旧代码前需恢复相应配置快照。实例 config.toml 仍不进入 Git。
