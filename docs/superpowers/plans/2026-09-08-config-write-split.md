# Config Write Routes Implementation Plan

**Goal:** Extract four approved POST routes without behavioral changes.
**Spec:** `docs/backend-config-write-split.md`
**Architecture:** Explicit frozen callback context; existing request guards and template transactions remain in the main service.
**Constraints:** Preserve this checkout's pending changes. No commit, push or deployment.

## Tasks

- [x] Add `tests/test_admin_config_routes.py`: real temporary template writes for each path, revision conflicts, unauthorized and cross-origin rejection, validation failures; unknown route must return False without touching dependencies. Run before extraction to capture behavior and the missing-module failure.
- [x] Create `hysteria/admin_config_routes.py` with `handle_write(handler, context, *, path, form) -> bool`. Move only the four approved branches, inject existing callbacks and exception types. Replace their original location with the dispatcher call; keep all common POST guards in place.
- [x] Register the module in all deployment inventories, copy, permissions and recovery whitelist. Adopt module/tests in `scripts/check-quality.sh`.
- [x] Run targeted template/concurrency/security tests, then `PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh`, shell syntax and diff whitespace checks. Review extraction equivalence and record results.
