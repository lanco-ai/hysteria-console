# Preview parity foundation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes.

**Goal:** Add safe fictional render coverage for all existing page families before React migration.

**Architecture:** Extend the existing loopback-only preview; reuse production renderers while supplying isolated test data and probe results. Test state never reaches production filesystem, network or service commands.

**Tech Stack:** Python pytest, existing SSR renderers, Playwright and current CSS.

**Spec:** `docs/superpowers/specs/2026-09-12-personal-site-refactor.md`

## Global Constraints

- Preserve current URLs, UI and business logic; no production edits or deploy.
- Preserve filesystem/network/process guards in isolated_preview.
- Do not interpret button visibility as successful mutation verification.
- No new AI, reminder, database, registration or payment features.
- This milestone does not complete the full React/FastAPI goal.

### Task 1: Missing page fixtures and safe rendering

**Files:** Modify `tests/workspace_preview_server.py`; add `tests/test_preview_page_parity.py`; extend `tests/workspace_visual.cjs` only after page coverage passes.

**Interfaces:** Consume existing `preview_server()` and `isolated_preview(directory)` contexts and current `subscription_service` renderers. Produce GET preview routes for `/`, `/admin/health`, `/admin/incidents`, `/admin/landing-egresses`, `/admin/logs`, `/admin/user/demo_alex`, `/user/change-password`, `/logout`, `/user/logout`; preserve existing routes and 405 for POST.

- [x] Write parameterized real HTTP tests: each route returns 200 HTML and its page-specific heading or control. Existing server must fail for missing routes.

```python
@pytest.mark.parametrize('route', ['/', '/admin/health', '/admin/incidents', '/admin/logs'])
def test_preview_document(route):
    with preview.preview_server() as server:
        with urlopen(f'http://127.0.0.1:{server.server_port}{route}') as response:
            assert response.status == 200
            assert response.headers.get_content_type() == 'text/html'
            assert b'<html' in response.read()
```

- [x] Run `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_preview_page_parity.py`; verify missing-route failure.
- [x] Implement explicit route dispatch calling current renderers. Inspect renderer dependencies before supplying fixed-clock fictional data; substitute external probes at their IO seam, not the renderer. Missing resources still return 404, writes 405.
- [x] Add per-page content assertions and verify guards with `tests/test_preview_isolation.py`. Test repeated page rendering and no leaked service/network calls.
- [x] Extend browser checks to missing pages at widths 1920, 1024, 390; inspect actual output and page errors. Do not change production styling to hide baseline defects; record any existing defect.
- [x] Run `npm run check:frontend`, relevant pytest tests and backend lint/format gates. Review the diff and record results before commit.

## Subsequent milestones (not claimed complete by this plan)

FastAPI authentication/error/read boundary and first typed React page follow this baseline. Detailed API contracts require current service-seam inspection, not wholesale reuse of the donor router. Full page migration, immutable release packaging, approved cutover and legacy cleanup remain required by the parent spec.

## Baseline findings and scoped fixes

- Nine missing document routes initially failed with 404. Preview now uses the real renderers, isolated logs/update/alert fixtures, and external-probe substitutions.
- The browser matrix covers 17 routes at three widths; this is render/interaction coverage, not screenshot-golden equality or mutation-success coverage. Landing-egress data remains an empty fixture.
- Existing health and incident KPI cards overflowed at 390px (document widths 453px and 392px). A failing geometry assertion was added before the shared mobile two-column fix. Desktop retains four columns.
- Incident dual panels were cramped side by side on mobile. A failing stacking assertion precedes the mobile single-column rule; desktop remains unchanged. These are explicit baseline fixes, not suppressed checks or a theme redesign.
- Current integration seam investigation: retain IdentityService credential-generation checks, request_multiplier_snapshot, strict-state failure handling and operational fail-closed behavior when adding FastAPI. The Hysteria auth_service is not the panel login service.
- No production deployment, proxy/configuration change, React page migration or FastAPI runtime adoption is included in this milestone.
- Final verification: 33 focused HTTP/isolation tests passed; backend lint/format gates passed; `npm run check:frontend` passed, including seven unit/gate tests and all three browser scripts. Independent review's health-fragment representation mismatch was fixed with red/green tests and re-reviewed. The full backend test suite was not rerun for this test-fixture/CSS-only milestone. Locally committed as e7dd999; not pushed or deployed.
