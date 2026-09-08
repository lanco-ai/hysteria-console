# Frontend Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Normalize the existing frontend without changing business behavior or the five-column management layout.

**Architecture:** Keep Python rendering and vanilla JavaScript. Establish an isolated browser harness, split styles without changing cascade order, then extract page scripts and shared utilities with behavioral coverage.

**Tech Stack:** Python, pytest, Node.js, Playwright, CSS and vanilla JavaScript.

**Spec:** `docs/superpowers/specs/2026-09-08-frontend-normalization-design.md`

## Global Constraints

- No React migration, production writes or deployment in this implementation phase.
- Preserve API, authorization, billing, URLs, five-column management and current neutral visual direction.
- Tests use fictional state and loopback services; no production credentials.
- Static resource changes must retain cache invalidation and deployment/recovery coverage.

## Task 1: Reproducible frontend validation

Files: `tests/workspace_preview_server.py`, new preview isolation tests, `package.json`, lock file, frontend check configuration, browser runner, `.github/workflows/backend-quality.yml`.

- [x] Write a failing isolation test: enter `isolated_preview(tmp_path)` and assert all service Path constants outside the source directory are redirected under `tmp_path`; leaving the context restores originals.
- [x] Add fixture context using `pytest.MonkeyPatch.context()`, redirect runtime paths before seeding fictional data, and reject unplanned external operations.
- [x] Run isolation tests; exercise real rendered admin and user pages against denied production access.
- [x] Add pinned frontend development tools and lint commands; use non-mutating checks in CI.
- [x] Make browser runner own preview server lifecycle, dynamically select loopback port, wait for readiness and clean up after failure.
- [x] Run existing layout and refresh browser checks through that runner; add page-error and resource-failure assertions.

## Task 2: CSS organization

Files: `hysteria/admin.css`, new ordered CSS source sections and build/check tool, CSS assembly tests.

- [x] Capture original stylesheet assembly and browser layout baseline.
- [x] Add a failing assembly test that rejects a stale checked-in public bundle.
- [x] Split complete rules by responsibility without altering their relative cascade order; retain `/static/style.css` as the deployed entry.
- [x] Validate generated bundle equality before component adjustments and run responsive browser tests.
- [x] Consolidate verified redundant rules into their owning sections; do not delete dynamically referenced selectors from text-search evidence alone.

## Task 3: Page script boundaries

Files: login, user, configuration and shell views; static scripts; static resource serving and deployment manifests.

- [x] Add behavioral browser tests for password toggle, subscription selection/copy/QR, shell controls and configuration interactions.
- [x] Extract each inline executable script to a named static resource, preserving initialization ordering.
- [x] Pass dynamic data using escaped attributes or non-executable JSON; test malicious strings cannot break the data boundary.
- [x] Register resources with content-based caching and deployment/recovery coverage; test missing and forbidden resource paths.

## Task 4: Shared utilities

Files: shared frontend utility module and its consuming page scripts; Node behavioral tests.

- [x] Write tests for byte formatting, escaping and request success/rejection/timeout/cancellation before implementing exports.
- [x] Extract only genuinely shared behavior; preserve per-page polling schedules and lifecycle ownership.
- [x] Verify hidden-page pause, failure recovery, refresh deduplication and history invalidation in browser tests.

## Task 5: Component consistency and final verification

Files: component CSS sections and view markup, frontend maintenance documentation.

- [x] Replace fixed inline form spacing with component classes while retaining dynamic progress widths.
- [x] Use semantic variables for backgrounds, text, borders, actions and status without flattening public/workspace differences.
- [x] Check affected layout pages at desktop, tablet and mobile sizes, including overflow, dialogs and controls; exercise polling errors and recovery separately. Codex helper integration is statically reviewed, not browser-covered.
- [x] Run frontend gates and full backend suite; review static deployment/caching and business behavior invariants.
- [x] Document commands and ownership; review git scope before offering integration/release.

## Verification result (2026-09-08)

- `npm run check:frontend`: passed CSS freshness, JS/CSS lint, seven Node tests,
  and all three isolated browser scenarios.
- `scripts/check-quality.sh`: passed static checks and 1457 pytest tests;
  68 datetime deprecation warnings remain outside this frontend change.
- Independent review: no Critical/Important findings. Added network resource-failure
  detection; optional Codex browser smoke coverage remains documented.
- Whitespace review: split CSS retains section-ending blank lines for byte-exact
  assembly; `git -c core.whitespace=-blank-at-eof diff --cached --check` passes.
  At implementation handoff, changes remained on `refactor/frontend-normalization`;
  no implementation commit, push or production deployment performed.
- `.codex-guard/` is unrelated local tooling and excluded from the intended scope.
