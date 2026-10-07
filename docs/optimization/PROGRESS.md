# 代码优化进度

状态：完成（第二轮代码已完成，部署待确认）

分支：`claude/code-optimization`（基于 main @ d8c647e）。只在这个分支提交和推送，不碰 main，不部署。

## 已完成

- 审查报告第一版：`docs/optimization/REPORT.md`
- P2：健康页只读一次校准文件（600d336）
- P3：评估后不做（理由见报告）
- P1：校准文件改为紧凑 JSON（c19914f）

## 下一步

第二轮已完成：D1 和 D2 已实现；D6 不需要改代码，只需在生产执行；D3、D4、D5 评估后暂不做。等仓库主人确认后再部署：前端新版本、两个后端模块，以及 D6 的生产配置。

## 测试方法（在生产机上）

- 测试放在隔离环境里跑：`/root/hysteria` 和 `/usr/local/etc/xray` 换成空的只读目录，`/run` 换成私有目录，网络放进独立命名空间。
- 依赖来自 `/dev/shm/tuic-accounting-tools`（加在 `PYTHONPATH` 里）；grpcio 用 `/tmp/tuic-stats-stage-20261007-verified/venv`。
- 用 `systemd-run --scope -p MemoryMax=450M` 限制内存。
- 全量测试约 6.5 分钟；还要跑 `bash scripts/check-quality.sh --lint-only`。
