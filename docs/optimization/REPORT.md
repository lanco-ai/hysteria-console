# hysteria-console 代码优化报告

## 总结

- **已实现**（在 `claude/code-optimization` 分支，没有部署，也没有合并 main）：
  - P1 校准文件改为紧凑 JSON：流量统计每轮少约 100 ms CPU，每天少写约 1 GB。
  - P2 健康页只读一次校准文件：每次刷新从 153 ms 降到 93 ms。
  - 全量 2,868 个测试和 lint 都通过。
- **需你决定**（按推荐顺序）：
  1. D1 前端按路由拆包。对客户体验收益最大，需要在本地或 CI 构建并跑浏览器测试验证。
  2. D6 停用已经没人用的 `hysteria-subscription` 服务，省约 27 MB 内存。
  3. D2 校准样本按时间保留，并新增整个结算周期的网卡汇总。
  4. 其余几项：D3 TUIC 统计子进程、D4 拆分超大文件、D5 聊天删除加索引。
- **不建议做**：P3、D7，原因见表格。


基线：`main` @ d8c647e（2026-10-07）。测量环境：生产机 ck-vps，1 核 CPU、913 MiB 内存。

## 现状测量

- 流量统计（`hysteria-traffic-limiter`）每 90 秒跑一次，单次耗时 1.1 秒，CPU 0.68 秒，每天合计约 11 分钟 CPU。
  - 其中校准文件 `state/cost_calibration.json` 读 34 ms、写 131 ms：文件 3.6 MB，2 万条样本，带缩进，每轮整个重写一遍并 fsync，每天约 960 次、3.5 GB 写入。
  - TUIC 统计子进程每轮约 108 ms CPU、34 MB 内存；模块导入约 70–100 ms。
- 前端只有一个 1,027 KB 的入口包（只有视频画布是懒加载），公开商品页也要下载整个后台控制台的代码。

## 发现与建议（按收益高、风险低排序）

| 编号 | 位置 | 问题 | 收益 | 风险 | 工作量 | 建议 | 状态 |
|---|---|---|---|---|---|---|---|
| P1 | `hysteria/cost_calibrator.py` `update_sample` | 3.6 MB 校准文件每 90 秒带缩进整体重写 | 每轮少约 100 ms CPU、少写 1 MB（−28%） | 低：只改序列化格式，读取方式不变 | 小 | 改为紧凑 JSON | 已实现（c19914f） |
| P2 | `hysteria/subscription_service.py` `_build_health_json_payload` | 健康页每次请求把 3.6 MB 校准文件解析两遍 | 每次刷新少约 35 ms 以上 | 低 | 小 | 读一次，供两处汇总复用 | 已实现（600d336，实测 153 ms → 93 ms） |
| P3 | `hysteria/cost_calibrator.py` `_recent_samples` | 每个统计窗口都把 2 万个时间戳逐个解析（每次健康页请求 4 遍） | 每次刷新少几十 ms | 低：样本按时间顺序追加 | 小 | 从尾部向前扫描，到窗口起点就停 | 不建议：P2 之后每次只能再省约 44 ms，而提前停止会改变“时间戳无法解析也保留”的语义，加缓存又要多占约 4.5 MB 面板内存 |
| D1 | `frontend/src/main.tsx` | 20 个页面全部静态导入，入口包 1,027 KB（gzip 后 297 KB）；KaTeX 只在 AI 聊天的 `features/chat/ChatMessage.tsx` 里用，却和整个后台一起打进了公开商品页 | 客户打开商品页时，后台代码和 KaTeX 都不用再下载，跨境和移动网络最明显 | 中：涉及路由和加载态，需要浏览器测试 | 中 | 按路由用 `React.lazy` 拆分：公开页、用户面板、管理后台各一组 | 需你决定（服务器内存不够构建，需在本地或 CI 构建验证） |
| D2 | `hysteria/cost_calibrator.py` `MAX_SAMPLES` | 上限 20160 条按“30 秒一次、保留 7 天”设计，实际 90 秒一次，所以保留了 21 天；而汇总最长只用 7 天 | 若按时间只保留约 8 天，文件变成约 1/3，IO 和 CPU 也同比下降 | 低 | 小 | 方案 a：只按时间保留 8 天。方案 b（推荐）：细样本保留 8 天，另存整个结算周期的每小时网卡汇总，用来和服务商账单对账 | 需你决定（会减少保留的历史数据） |
| D3 | `hysteria/tuic_user_meter.py` `observe` | 每轮启动一个 Python 子进程加载 grpcio（约 108 ms CPU、34 MB 内存） | 每天少约 1.7 分钟 CPU | 中：会动到隔离设计 | 中 | 常驻轻量查询进程，或给统计服务的解释器装 grpcio | 需你决定（收益有限，现状可接受） |
| D4 | `subscription_service.py`（约 114 KB）、`deploy.sh`（约 100 KB）、`traffic_limiter.py`（约 45 KB） | 单文件过大，改动容易牵连 | 可维护性 | 高 | 大 | 按职责逐步拆分，每步都由全量测试兜底 | 需你决定 |
| D5 | `hysteria/web_api/chat_workspace_store.py` 删除会话 | 删除时全表扫描 tool_runs/tool_plans 并逐行解析 JSON | 数据量大了才明显 | 中：涉及 SQLite 表结构迁移 | 中 | 加 `conversation_id` 索引列 | 需你决定（目前数据量小） |
| D6 | `hysteria-subscription.service`（127.0.0.1:8081） | 统一面板已在进程内处理订阅，这个旧服务没有 nginx 路由、没有代码调用，24 小时内 0 次请求，却常驻约 27 MB（8 MB 内存 + 19 MB swap） | 913 MB 的机器上省出约 27 MB | 中：`deploy.sh`（11 处）、`hy2-deploy-recovery` 和若干测试还引用它 | 中 | 先从部署和恢复流程里摘掉，再停用并禁用这个服务 | 需你决定（属于部署变更） |
| D7 | `state/usage_hourly.json`、`protocol_usage_hourly.json` | 每轮带缩进重写（约 52 KB 和 44 KB） | 每轮少几 ms | 低 | 小 | 改紧凑 JSON | 不建议：收益很小，这两个账本有时要人工查看，可读性更重要 |

## 已复核、无需处理

- 安全扫描（ruff 的 bandit 规则，102 条）逐条复核，都是误报或已有防护：
  - SQL 拼接的只是代码里写死的表名，值都通过 `?` 参数绑定；
  - DOCX 解析前先拒绝 `DOCTYPE`/`ENTITY`；
  - `exec` 只在学习沙箱容器里运行，属于设计行为；
  - 其余是安全形式的 `subprocess`、`assert` 等提示。

## 测试耗时

- 全量测试在生产机（1 核）上约 6 分 20 秒。最慢的单项都在 6 秒以内，前 12 个加起来约 30 秒，大部分时间花在两千多个小测试上，没有明显的慢测试可以单独优化：
  - `test_deploy_supply_chain_hardening` 6.0 s；
  - TUIC 统计客户端的超时测试 3.0 s（故意等满 3 秒的 deadline）；
  - HTTPS 激活的崩溃恢复测试每个约 2 s，共 5 个；
  - 部署恢复的看门狗测试 2.6 s；
  - DOCX/PDF 提取 2.3 s。
- 建议（需你决定）：在 CI 里用 `pytest-xdist` 并行跑（GitHub 托管的 runner 至少有 2 个核），预计 CI 时间能减半；要新增一个开发依赖。
- 在新的工作目录里，有 6 个测试依赖已构建的前端 `frontend/dist`，CI 会先构建所以不受影响；本地跑之前要先构建，或者放入已发布的前端包。
