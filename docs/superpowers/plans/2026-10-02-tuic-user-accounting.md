# TUIC User Accounting Implementation Plan

> **For agentic workers:** Follow this bounded task package sequentially. The assigned expert is the sole writer and must not delegate or start another workflow. Use test-driven-development for accounting changes. No commits or production changes are authorized.

**Goal:** Count authenticated TUIC v5 payload upload/download per user from activation onward in the existing quota, display, and enforcement paths. Keep pre-activation aggregate traffic outside user quotas.

**Architecture:** Replace only the TUIC runtime when an operator later approves migration, using pinned SagerNet sing-box source built with QUIC and V2Ray statistics support. Read cumulative per-user counters without reset; use service invocation identity to detect restarts. Integrate increments with the existing canonical usage lock and a durable, replay-safe accounting transaction so write failure cannot advance a baseline independently of credited daily usage. Keep physical port counters as separate protocol diagnostics, never add both payload and tunnel totals to the same cost sample.

**Tech Stack:** Existing Python JSON/file-lock backend, TUIC v5, systemd, existing Xray gRPC CLI, pinned sing-box source with `with_quic,with_v2ray_api`. No new public listener or daemon. Frontend contracts remain unchanged: existing used bytes include the newly credited source.

**Spec:** User selected authenticated per-user accounting on 2026-10-02. Preserve existing external UDP9443, UUID/password, subscription format, certificate/key, h3 ALPN, BBR, disabled 0-RTT, expiry/disable/quota behavior. Grok containers were already stopped under explicit authorization; do not restart them.

## Global Constraints

- Production `/root/hysteria`, systemd units, credentials and existing user ledgers are read-only until a separate release authorization.
- No historical attribution, averaged allocation, arbitrary URL proxy, credential printing, or secret-bearing fixtures.
- Reuse existing usage lock, Shanghai buckets, raw application bytes and 2.28 display multiplier; preserve manual resets/refunds, cycle rollover and deleted users.
- No TUIC `--reset` stats collection. Bound query time/output; distinguish empty valid stats from failure.
- Preserve legacy TUIC mode for existing installations until explicit migration; fail safely in user-metered mode if accounting state/API/generation is untrusted.
- Tests must use temporary stores and isolated loopback ports. Never use live traffic?clear=1, Xray stats -reset, or actual users for tests.
- Source and runtime provenance must be verified. Official sing-box1.14.2 linux-amd64 binary was checksum-verified but lacks with_v2ray_api; do not ship it as working user metering.
- Server has one CPU, 913MiB RAM, about134MiB persistent disk free. Prefer a verified external build; if a bounded temporary local build is feasible, use only task-owned directories, capped memory/CPU/storage, and no system installation. Stop before compromising service availability. Production migration remains gated.
- No commit, push, main merge or deployment in this implementation stage. Maintain the same astra-luna task/budget (0/2 reworks,0/1 replans consumed).

## Task1: Verify and prepare a supported runtime

**Files:** Source/build/migration documentation; test-only runtime fixture and provenance under `.astra-luna/` or task-owned temporary directory.

- Establish pinned source/toolchain/checksum build recipe for QUIC plus V2Ray API, with no untrusted prebuilt fallback.
- Validate binary version feature tags and a real config check. Reuse `/usr/local/bin/xray api statsquery` against its isolated loopback API with reset omitted; verify actual interoperability.
- Exercise genuine TCP and native/stream UDP payloads for two fixture identities, denied third identity, IPv4/IPv6 listener, h3/TLS and preserved auth format.
- Record exact artifact hashes, commands and transport/counter results. Do not claim source feasibility as executed compatibility evidence.

## Task2: Implement transactional per-user collection

**Files:** `hysteria/tuic_meter.py` or a small separate user collector, `hysteria/traffic_limiter.py`, targeted state/transaction helper if needed; tests for collection and canonical quota integration.

- Write failing tests for normalized uplink→rx/downlink→tx and authenticating-user mapping; reject malformed/negative/duplicate counters, unknown identities and unsafe endpoint configuration.
- Observe invocation generation before/after sampling. Read cumulative values without clearing. Treat fresh verified generation as counters from zero; counter decreases within one generation are faults, not automatic baselines.
- Persist canonical daily credits and matching source checkpoint through a replay-safe transaction under usage.lock. Validate/recover transactions before other sources are destructively consumed. Cover crashes/write exceptions before and after each persistence boundary, repeated polling and concurrent collectors.
- Feed normal raw per-user deltas to existing display/quota/alerts/enforcement and hourly/protocol graphs. Do not charge old nft aggregates, and prevent dual inclusion in calibration.
- Cover period boundary, refund/reset, user expiry/disable/delete/token rotation, missing/corrupt state and stat/API failures. Preserve unaffected Hysteria/Xray behavior and fail-closed boundaries.
- Document the in-memory runtime limitation: an abrupt runtime crash can lose unpolled tail bytes; do not promise crash-perfect runtime accounting.

## Task3: Prepare opt-in config and migration

**Files:** `hysteria/tuic_config.py`, runtime launcher or opt-in systemd configuration, targeted migration/build helper, deploy module ownership lists as needed, docs and matching tests.

- Preserve old config handling; render/sync named sing-box TUIC users and stat users in every authorization/config update path once migrated. No stale credential or silently retained removed user.
- Keep gRPC stats listener loopback-only, separate from Xray10085; it has no application authentication and must never be public.
- Map or explicitly reject unsupported old runtime settings; preserve IPv6 relay policy and bind behavior rather than blindly copy flat keys.
- Provide a concrete preflight/dry-run/rollback path for later authorized migration, including source artifact trust, backups, old counter archival, fresh generation and quota checks. Do not apply it to production now.
- Explain activation time, historical limitations, expected brief TUIC reconnect and sampling-tail limits.

## Task4: Validate and review

- Run new targeted accounting/config/mutation/backend API tests, then `bash scripts/check-quality.sh` under production-isolated test environment. Record exact results and do not alter gates.
- If frontend files/contracts change, run applicable type/lint/build/browser gates; otherwise explain why frontend assets need no rebuild and validate existing API contracts with backend tests.
- Require real supported-runtime fixture validation. If artifact acquisition/build is unavailable, preserve implemented source and report that gate accurately instead of reporting a complete usable release.
- Controller inspects real diff/evidence and dispatches fresh GPT-6 Sol/high reviewer. Repair only within saved2-rework/1-replan budget.
- Final result states code/tests and current branch/worktree, Grok stopped, and no commit/push/deploy. Any production approval is requested only after concrete changes and checks are reviewable.
