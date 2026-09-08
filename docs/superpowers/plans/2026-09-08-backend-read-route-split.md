# Backend Read Route Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Continue the already-approved incremental work in the current checkout; preserve all first-round pending changes. Do not push or deploy.

**Goal:** Extract login presentation and seven read-only admin endpoints without changing HTTP behavior.

**Architecture:** Auth views depend only on explicit presentation callbacks. A read-route dispatcher uses a frozen callback context; the existing HTTP handler retains session exchange, security headers, request scope and failure handling.

**Tech Stack:** Python stdlib HTTP server, pytest, Ruff; no new runtime dependency.

**Spec:** `docs/backend-read-route-split.md`

## Global Constraints

- Preserve existing public signatures and rendered content.
- Preserve GET/HEAD, authorization, errors, query defaults and export headers.
- No production writes, deployment, commits of prior pending work or push.
- Keep the first-round dirty work intact.

## Task 1: Auth presentation

Files: create `hysteria/auth_views.py`, `tests/test_auth_views.py`; modify `hysteria/subscription_service.py`.

- [x] Capture current login body hashes using an identity page wrapper and fixed helper callbacks; test the new module before it exists (red).
- [x] Move `render_login` and `render_user_login` bodies with explicit `html_page`, `render_alert`, `icon`, `password_max_length` dependencies; keep old host-taking wrappers.
- [x] Run `python3 -m pytest -q tests/test_auth_views.py tests/test_admin_login_design.py tests/test_new_features.py`.

## Task 2: Read-only dispatcher

Files: create `hysteria/admin_read_routes.py`, `tests/test_admin_read_routes.py`; modify main service.

Interface: `handle_read(handler, ctx, *, path, query, host, send_payload) -> bool`; `ReadContext` holds auth, clock, page and payload callbacks. False means no response was sent; True means the request has been handled.

- [x] Add isolated HTTP tests for the seven paths listed in the spec, parametrized GET/HEAD and authenticated/unauthenticated. Add standalone unknown-route test before the dispatcher exists.
- [x] Move only those branches into small route functions; preserve public callback wrappers and place dispatch after Token exchange and before remaining admin GET branches.
- [x] Verify summary flags, CSV400 and Content-Disposition, health snapshot JSON and HTML401, missing credentials and HEAD empty bodies.

## Task 3: Deployment and verification

Files: `deploy.sh`, `scripts/hy2-deploy-recovery.py`, `scripts/check-quality.sh`, spec validation notes.

- [x] Register both modules alongside public_views in durable inventory, snapshot, copy and chmod; add recovery whitelist entries.
- [x] Add both modules and tests to strict Ruff F/I and formatting adoption list. Format only these adopted files.
- [x] Run `bash -n deploy.sh scripts/check-quality.sh` and the complete quality script with the existing quality venv.
- [x] Perform independent read-only review, fix actionable findings, then report exact results and remaining scope. Do not push or deploy.
