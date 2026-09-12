# FastAPI read boundary implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Introduce the real FastAPI session and administrator-overview read boundary without changing production routing.

**Architecture:** A side-effect-free app factory accepts an explicit adapter around subscription_service. The adapter calls existing identity, billing, snapshot and failure-policy services; typed response models allowlist public fields. Synchronous service work stays off the ASGI event loop.

**Tech Stack:** Python 3.12 development/CI, FastAPI, Pydantic, HTTPX TestClient, pytest. Pin verified dependencies in requirements-web.txt and requirements-dev.txt; no system Python installation.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- No production edits, deployment, proxy configuration changes, state migration or new product features.
- Preserve existing Python business services, credential-generation invalidation, strict-state failure behavior and request-local multiplier snapshots.
- API responses use explicit field allowlists: never serialize an entire runtime object containing password hashes, proxy secrets or account credentials.
- Existing URLs and bearer-exchange handlers remain unchanged. New JSON endpoints consume existing session cookies, not tokens in their query strings.
- Nginx remains the public entry; no public development listener or additional runtime worker.
- No React/legacy dual ownership. This task does not complete frontend migration.

### Task 1: Session and overview API with real service adapter

**Files:** Create hysteria/web_api/__init__.py, app.py, models.py, services.py; tests/test_web_api_reads.py; requirements-web.txt. Modify requirements-dev.txt and scripts/check-quality.sh. Existing production route modules remain unchanged.

**Interfaces:**

```python
# Public Python construction API, no module-level app or runtime IO.
from web_api import create_app
from web_api.services import LegacyPanelServices
app = create_app(LegacyPanelServices(subscription_service), max_requests=32)
```

The constructor stores the injected service module; never reads state or starts timers/listeners. max_requests must be a positive integer. No default adapter pointing at live state. Keep adapter logic and FastAPI request/response logic separate.

Registered routes (GET and HEAD, identical status/headers, empty HEAD body):

| Route | Behavior |
| --- | --- |
| /api/v1/session | Existing sid wins if valid: 200 `{"role":"admin"}`. Otherwise valid usid plus user_panel_access_error checks: 200 `{"role":"user","username":"demo_alex"}`. No valid session: 401 `{"error":"login_required"}`. User lifecycle failure: 403 with existing error code. |
| /api/v1/admin/overview | Existing sid required. Unauthorized including usid-only: 401 `{"error":"login_required"}`, same as old overview JSON. Successful response matches old builder's fields and values. |

Overview DTO fields: ts:str, total_used:int, users:list of user:str, tx:int, rx:int, used:int, total:int, percent:float, online:int, revision:str, disabled:bool. Unknown builder fields are excluded at both levels. Do not clamp/round/recalculate business values in the adapter.

Factory disables docs/redoc/openapi and slash redirects for this internal prototype. Unregistered paths return 404 JSON; POST to reads returns 405 JSON. No SPA/static fallback. All responses, including errors, carry Cache-Control:no-store, X-Content-Type-Options:nosniff, Referrer-Policy:no-referrer, X-Frame-Options:DENY and Cross-Origin-Opener-Policy:same-origin. Error payloads never include exception text, paths, credentials or tracebacks.

Service request bridge contains only headers and a path WITHOUT query credentials. Use existing is_logged_in and get_logged_in_user_context; no new session store. Execute the complete synchronous read inside existing request_multiplier_snapshot so repeated reads use one multiplier and concurrent requests cannot share snapshots.

StateStoreError/OSError become 503 `{"error":"state_unavailable"}`. Before returning, call existing _state_failure_requires_static_stop and, when required, existing _fail_closed_static_access. This policy is synchronous too. Unexpected exceptions return sanitized 500 `{"error":"internal_error"}`. Do not claim stopped services were confirmed. Tests substitute the external stop seam and never call system services.

Admission must be bounded before dispatching synchronous work: at most max_requests active service requests; excess returns 503 `{"error":"server_busy"}` with Retry-After:1. Release capacity on success, errors and cancellation, but never release while cancelled non-abandoning worker work is still accessing state. Use an ASGI-safe request boundary with try/finally and nonblocking capacity acquisition. No global threadpool configuration.

- [x] Install pinned FastAPI 0.141.1 and HTTPX 0.28.1 into /tmp/hy2-quality-venv only; verify package metadata/Python floor. Add runtime requirements file and dev include so CI cannot silently skip API tests.
- [x] Write failing tests using temporary real state and sessions (reuse isolated_preview or reliability fixture after mapping all required paths). Name breaks: missing route; invalid credential accepted; user can read admin; disabled user accepted; credential rotation ignored; query token bypass; state failure becomes empty success; secret leakage; leaked capacity.

```python
def test_overview_requires_admin(api_client):
    response = api_client.get('/api/v1/admin/overview')
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}

def test_unknown_api_does_not_fall_back(api_client):
    response = api_client.get('/api/v1/missing')
    assert response.status_code == 404
    assert response.headers['content-type'].startswith('application/json')
```

- [x] Run focused pytest, record failures before implementation; missing package errors alone are not red evidence. An empty factory scaffold may allow boundary assertions to run and fail with 404 before routes exist.
- [x] Implement models and adapter. Test real authenticated overview values against hand-derived fictional tx/rx/quota/online data, plus old/new parity over the same fixed clock. Exercise admin credential replacement, session expiration, user subscription-token invalidation, disabled/expired/password-change-required user states. Retain old endpoint behavior unchanged.
- [x] Implement factory, security/error boundary and bounded admission. Use HTTPX/TestClient concurrent requests and an event-gated service IO seam to prove overload and recovery; assert real HTTP outcomes, not mocked methods. Check handlers execute off the ASGI event-loop thread and request multiplier context resets after failures. Inject extra secret-shaped builder fields solely to prove response allowlisting.
- [x] Test factory construction causes no filesystem/network/service calls; use existing isolation guards. Test GET/HEAD, no automatic docs, missing path, wrong method and sanitized internal errors. Do not mount this app in the old server or add a production service file.
- [x] Run focused API tests, affected identity/accounting/error regression tests, backend lint/format and full backend suite once. Add all new Python files to adopted quality checks. Run pip check and bash -n deploy.sh. Independent task review and local commit; no push/deployment.

## Later scope retained

Verified locally at c7cced6 after independent review: 42 API tests passed. Before the review fix, the single full backend run passed 1517 tests; the two added recovery regressions and all API tests passed after it. Lint/format and dependency checks passed. Two upstream dependency deprecation warnings and existing datetime warnings remain documented, not filtered. No production mount or deployment.

This is the API foundation gate, not the migration's final state. Typed read-only React vertical slice follows, then all mutation flows/pages, paired staging validation, explicitly authorized production cutover, and obsolete rendering cleanup. No mutation endpoint or conflict response is fabricated in a read-only task; mutation validation, CSRF and revision-conflict tests belong with the real write adapters.
