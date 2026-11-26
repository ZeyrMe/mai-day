## 0.2.0 · v2.5 业务增量

块级 max_shift_minutes/negotiable/reply_weight、查档期 free/negotiable/conflict_locked、真实当前消息自记动态、作息偏好读取、可选午觉和每日标题润色。max_shift_minutes/reply_weight 在 TOML 中用 -1 表示继承（JSON 的 null 也兼容），0 明确表示不偏移/不加权。catchup 过滤宿主自身消息与已标记 Bot，至少99条才摘要，按上限读最新记录并以 replyable=false 插卡，同睡眠周期去重、失败保留重试。唯一频率合成器按会话与关系相乘、可选归一化，停用/卸载条件恢复。控制台有临时唤醒、频率/爬楼状态、无外网时间线图片和保留原底稿的未来软安排重排。

## 未发布 · v2.5 Batch 1

- 新增独立 scope、群/私聊身份判定与公共集合软联动。
- 新增原生状态面板和缓存刷新按钮；改进中文结构化配置，兼容旧格式。
- 完整业务升级仍按后续批次实施，本批未部署到运行卷。

# 0.1.0

首次开发版本；运行验收前不发布正式 Release。
