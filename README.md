# 麦的一天

`zeyr.mai-day` · 日程、连续动态与不丢消息睡眠。

开发基线 MaiBot 1.4.8 / SDK 2.23.0。可独立安装，其他 zeyr 插件仅通过公开 API 自动联动。

首批已实现：student/worker/night_owl/custom 周模板、locked/soft/open 三级刚度、四档波动、三日连续状态窗口、每日底稿与只增动态流、睡眠债和聊嗨延睡、手动 `/日程 + HH:MM ... [locked|soft|open]`、`/唤醒`、`get_state`/分享租约 API，以及睡眠时只静默回复和发送、持续接收入库学习的双保险。主动问候采用独立 Replyer 生成后按实际发送回执结算；观察群始终只采集。

分享流程是 `reserve_share` → `authorize_share`（精确文本短期授权）→ 实际发送 → `settle_share`，发送前会再次检查窗口。没有 calendar/circle/insights 时仍可独立运行，并使用本地配置或降级路径。

仍需在已升级的真实 MaiBot 1.4.8 宿主上做 Hook/发送验收，当前离线测试不等同于生产上线。

安装时将整个目录同步到宿主 plugins；运行 config.toml 由 SDK 生成。实例名单、个人资料和密钥只填运行配置，不提交。

检查：`python -m pytest`，开发依赖见 pyproject.toml。
