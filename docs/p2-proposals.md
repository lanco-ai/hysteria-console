# P2 提案（本轮不实现）

## 1. 自动更新执行器

复用 `hysteria_update.apply_update()`。策略文件默认 `enabled=false`。

调度条件（全部满足才执行）：

- `enabled == true`
- 当前不在 cooldown
- 发布年龄 ≥ `min_release_age_hours`
- 若 `skip_if_online_users`：`online.json` 无在线用户
- 维护窗口（若配置）命中
- `probe_recent_backup` 新鲜
- 非 urgent 走稳定通道；urgent 可跳过年龄门槛但仍需备份

进程被 kill：启动时 `reconcile()` 看到 `pending_confirm` + last-good 即回滚。

## 2. 每用户家宽出口（独立高风险项目）

真正“指定出口”需要 Xray routing / inbound tag 映射，会进入授权链。

**禁止**把出口选择放进 `authorization_config_error` 或 `_build_static_access_plan` 的现有 fail-closed 判定而不做完整设计。

本轮落地字段仅为展示。

## 3. panel / sub token 拆分（暂不推荐）

分享对象即用户本人，面板页已展示订阅链接。拆分不减少实际泄露后果，却会使存量链接失效。仅当出现“只读分享面板（不含订阅 URL）”需求时再评估：`panel_token` 缺省 fallback 到 `sub_token`。
