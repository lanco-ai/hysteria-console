# React + Vite Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the complete React + Vite panel redesign through four independently reviewable, sequentially dependent implementation plans.

**Architecture:** The Python API is completed first while the legacy UI remains live; the React app then reaches functional parity; the approved visual/Three.js layer is added behind fallbacks; finally deployment cuts over atomically and removes all legacy frontend code. Each phase must meet its acceptance gate before the next begins.

**Tech Stack:** Python 3, React 19.2.8, TypeScript 7.0.2, Vite 8.2.2, TanStack Query, Motion, ECharts, Three.js/R3F, pytest, Vitest, Playwright, nginx, systemd

**Spec:** `docs/superpowers/specs/2026-08-30-react-vite-panel-redesign.md`

## Global Constraints

- Execute plans in the listed order; later plans consume interfaces and tests created earlier.
- Use TDD and the task-level commits specified in each plan.
- Keep the old UI production-authoritative until the cutover plan's activation task passes.
- Never weaken security/recovery tests to make the migration pass.
- Never stage or overwrite `hysteria/clash-default.yaml.tpl` or `tests/test_clash_template.py` unless a test command reads them.
- Completion means the cutover plan's final acceptance, not completion of an earlier dual-run phase.

---

## Spec Coverage Map

| Spec sections | Implemented by |
|---|---|
| 1–3 motivation, goals, non-goals | global constraints in all four plans |
| 4 target architecture | API Tasks 1–8; cutover Tasks 3–5 |
| 5 URL/routing contract | API Task 3; feature Task 3; cutover Task 4 |
| 6 Python backend/API/security/concurrency | API Tasks 1–8 |
| 7 React application/state/polling/forms | feature Tasks 1–9 |
| 8 visual system, motion, Three.js, accessibility | feature Tasks 3–9; visual Tasks 1–5 |
| 9 error and degradation design | API Tasks 1–7; feature Tasks 2–9; visual Task 2 |
| 10 security design | API Tasks 2–8; feature Tasks 2–9; cutover Tasks 1–7 |
| 11 build, deployment, rollback | cutover Tasks 1–5 |
| 12 migration sequence | this master plan's Tasks 1–4 |
| 13 testing strategy | all plan phase gates; cutover Task 7 final audit |
| 14 performance budgets | feature Task 6; visual Task 5 |
| 15 documentation | API Task 8; cutover Task 7 |
| 16 completion criteria | cutover Tasks 6–7 and final audit |

---

### Task 1: Complete the Python API phase

**Files:**
- Execute: `docs/superpowers/plans/2026-08-30-panel-api-refactor.md`

**Interfaces:**
- Produces: complete `/api/v1`, shared backend services/repositories, legacy/API parity

- [ ] **Step 1: Execute every checkbox in the API plan**

Run each task's failing test, implementation, passing test, and commit exactly as written in `2026-08-30-panel-api-refactor.md`.

- [ ] **Step 2: Verify the phase acceptance gate**

Run: `pytest -q && ./scripts/hy2-preflight.sh`

Expected: all Python and preflight checks pass while the legacy UI remains operational.

### Task 2: Complete React functional parity

**Files:**
- Execute: `docs/superpowers/plans/2026-08-30-react-panel-feature-parity.md`

**Interfaces:**
- Consumes: `/api/v1` contracts from Task 1
- Produces: production Vite build and full functional browser suite

- [ ] **Step 1: Execute every checkbox in the feature-parity plan**

Run each task's tests and commits exactly as written in `2026-08-30-react-panel-feature-parity.md`.

- [ ] **Step 2: Verify the phase acceptance gate**

Run: `cd frontend && npm run typecheck && npm run lint && npm run test && npm run build && npm run e2e`

Expected: all React routes and workflows pass under the production build; production traffic still uses legacy HTML.

### Task 3: Complete visual and Three.js phase

**Files:**
- Execute: `docs/superpowers/plans/2026-08-30-react-panel-visuals.md`

**Interfaces:**
- Consumes: functional React pages from Task 2
- Produces: final design system, two bounded scenes, fallbacks, bundle gates

- [ ] **Step 1: Execute every checkbox in the visual plan**

Run each task's tests and commits exactly as written in `2026-08-30-react-panel-visuals.md`.

- [ ] **Step 2: Verify the phase acceptance gate**

Run: `cd frontend && npm run typecheck && npm run lint && npm run test && npm run build && npm run check:bundle && npm run e2e -- visuals.spec.ts`

Expected: visual, reduced-motion, WebGL fallback, and budget checks pass.

### Task 4: Cut over atomically and remove legacy frontend

**Files:**
- Execute: `docs/superpowers/plans/2026-08-30-react-panel-cutover.md`

**Interfaces:**
- Consumes: complete API, React app, and visual layer
- Produces: final production architecture and completion evidence

- [ ] **Step 1: Execute every checkbox in the cutover plan**

Run each task's tests and commits exactly as written in `2026-08-30-react-panel-cutover.md`.

- [ ] **Step 2: Run the final requirement-by-requirement audit**

Run: `./scripts/hy2-preflight.sh`

Run: `cd frontend && npm run e2e`

Run: `pytest tests/test_deploy_durable_recovery.py tests/test_panel_deploy_integration.py tests/test_panel_nginx.py tests/test_panel_cutover.py -v`

Expected: all commands pass and every completion criterion in spec section 16 has direct evidence.
