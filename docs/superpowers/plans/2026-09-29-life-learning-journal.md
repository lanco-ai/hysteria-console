# Life Learning Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable daily timeline, structured AI/storage and IELTS notes, thought records, and reviews to the existing plan page.

**Architecture:** Keep the current plan store and four-quadrant UI intact. Add an independent private journal store and authenticated API, then mount focused timeline and review components beside the existing plan view. Filter/search on the server and use record revisions for safe edits.

**Tech Stack:** FastAPI, Pydantic, existing `state_store`, React 19, TypeScript, CSS, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-29-life-learning-journal.md`

## Global Constraints

- Implement only in `/root/hy2/.worktrees/life-learning-journal`; do not touch live deployment or credentials.
- Do not commit, push, deploy, install, restart services, or alter production data.
- Preserve all existing plan behavior and data, including reminder and assistant flows.
- Write meaningful behavior tests first and observe failure before implementation.

---

### Task 1: Private journal data and API

**Files:** Create `hysteria/web_api/journal_service.py`, `hysteria/web_api/journal_routes.py`, `tests/test_journal_feature.py`; modify `hysteria/web_api/app.py` to register routes.

**Interfaces:** `JournalStore.read(filters)` yields validated records and revision; `JournalStore.create/update/delete` mutate one record under a lock; routes serve `/api/journal`, `/api/journal/{id}`, `/api/journal/export`. Record fields include `id`, `kind`, `occurred_at`, optional `ended_at`, `timezone`, `title`, `body`, type-specific fields, `created_at`, `updated_at`, revision.

- [ ] Write a failing store/API test that creates an AI paper record, reads it by local date, updates it with revision, and verifies persistence and private permissions.
- [ ] Run `PYTHONPATH=hysteria python3 -m pytest -q tests/test_journal_feature.py` and confirm the new test fails for a missing journal implementation.
- [ ] Implement validated atomic storage, revision conflict detection and admin-only same-origin routes with bounded request bodies.
- [ ] Run the focused tests and confirm create, edit, search/filter, delete, export, auth and error behavior pass.

### Task 2: Time line and structured entry editor

**Files:** Create `frontend/src/features/plans/journalApi.ts`, `JournalPage.tsx`; modify `frontend/src/features/plans/PlansPage.tsx`, `frontend/src/styles/sections/23-plans.css`, navigation/document titles; add `tests/react_journal_browser.cjs` and connect it to the browser runner.

**Interfaces:** The journal component accepts selected date and timezone from `PlansPage`, owns its record draft and API state, and exposes no plan-store mutation. Type-specific fields map to the API names from Task 1.

- [ ] Write a failing browser test: create a timestamped IELTS entry, reload, verify it appears in order, edit, filter, then open the existing plan view.
- [ ] Run the browser test and confirm its first missing journal control fails.
- [ ] Implement default timeline, quick type selection, auto timestamp with manual backfill, structured templates, search, edit/delete, and responsive layout.
- [ ] Run the journal browser test and existing plan browser tests; repair only regressions introduced by the new views.

### Task 3: Daily and weekly review with export

**Files:** Extend `JournalPage.tsx`, `journalApi.ts`, `23-plans.css`, `tests/test_journal_feature.py`, `tests/react_journal_browser.cjs`.

**Interfaces:** Daily review and weekly review are first-class journal records; weekly summary derives counts by type for a Monday–Sunday range. Export downloads only authenticated private data.

- [ ] Extend failing tests for daily learning-state evidence, weekly review persistence and week counts, and JSON export authorization.
- [ ] Run tests to see the missing review/export behavior fail.
- [ ] Implement review forms, bounded counts, weekly navigation and download action.
- [ ] Run focused API/browser tests, typecheck, build and relevant existing plan tests.

### Task 4: Verify delivery

**Files:** Review all modified files and this spec.

- [ ] Inspect the actual diff for scope, privacy, data-loss and time-zone edge cases.
- [ ] Run focused Python and browser tests plus `npm run typecheck:react` and `npm run build:react`.
- [ ] Record any remaining limits and provide a reviewable worktree path; do not deploy.
