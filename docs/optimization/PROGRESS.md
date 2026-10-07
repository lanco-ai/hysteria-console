# 代码优化进度

状态：进行中

分支：`claude/code-optimization`（基于 main @ d8c647e）。只在这个分支提交和推送，不碰 main，不部署。

## 已完成

- 审查报告第一版：`docs/optimization/REPORT.md`
- P1：校准文件改为紧凑 JSON

## 下一步

1. P2：健康页只读一次校准文件，供两处汇总复用（`_build_health_json_payload` 和 `health_widgets.summarize_cost_calibration`）。
2. P3：`_recent_samples` 从尾部向前扫描，到窗口起点就停。
3. 用 `pytest --durations=20` 找出最慢的测试，补进报告。
4. 低风险项做完后，在 REPORT.md 开头写总结，把状态改为“完成”。

## 测试方法（在生产机上）

- 测试放在隔离环境里跑：`/root/hysteria` 和 `/usr/local/etc/xray` 换成空的只读目录，`/run` 换成私有目录，网络放进独立命名空间。
- 依赖来自 `/dev/shm/tuic-accounting-tools`（加在 `PYTHONPATH` 里）；grpcio 用 `/tmp/tuic-stats-stage-20261007-verified/venv`。
- 用 `systemd-run --scope -p MemoryMax=450M` 限制内存。
- 全量测试约 6.5 分钟；还要跑 `bash scripts/check-quality.sh --lint-only`。
