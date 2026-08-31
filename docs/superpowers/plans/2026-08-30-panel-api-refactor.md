# Panel API Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain the existing Python panel and proxy business semantics while exposing every React-facing read and mutation through a typed, secure `/api/v1` JSON API.

**Architecture:** Keep `BoundedThreadingHTTPServer` and the loopback-only service, introduce a small exact-match/parameter router, and move reusable policy/data access behind focused service modules. Legacy HTML/form routes remain active during this plan so API behavior can be compared before the frontend cutover.

**Tech Stack:** Python 3 standard library, existing `state_store`/policy modules, pytest, JSON over same-origin HTTP

**Spec:** `docs/superpowers/specs/2026-08-30-react-vite-panel-redesign.md`

## Global Constraints

- Preserve HttpOnly `sid`/`usid`, `SameSite=Lax`, conditional `Secure`, 24-hour lifetime, generation binding, and session caps.
- Preserve token-to-cookie `303` exchanges and never expose credentials in general read endpoints, logs, drafts, URLs, or error bodies.
- Every dynamic/API response is `Cache-Control: no-store`.
- Authenticated mutations require the session-derived CSRF header plus existing same-origin checks.
- Preserve `user_revision`, `template_revision`, landing registry revisions, idempotency keys, recovery receipts, reload queues, and fail-closed behavior.
- Do not modify or stage the operator's existing changes in `hysteria/clash-default.yaml.tpl` or `tests/test_clash_template.py`.
- Keep the legacy renderer operational until the cutover plan explicitly removes it.

---

## File Structure

| Path | Responsibility |
|---|---|
| `hysteria/panel_api/router.py` | exact method/path-template matching and dispatch |
| `hysteria/panel_api/responses.py` | success/error envelopes and no-store JSON writing |
| `hysteria/panel_api/schemas.py` | strict primitive validation and safe serializers |
| `hysteria/panel_api/auth.py` | session views, role guards, and CSRF verification |
| `hysteria/panel_api/session.py` | session bootstrap and login/logout handlers |
| `hysteria/panel_api/admin_users.py` | admin overview/user read and mutation endpoints |
| `hysteria/panel_api/user_panel.py` | user panel, password, rotation, and egress endpoints |
| `hysteria/panel_api/usage.py` | usage/history/refresh/reset endpoints |
| `hysteria/panel_api/operations.py` | health, incidents, logs, Codex, reload, updater endpoints |
| `hysteria/panel_api/configuration.py` | settings, cycle, cost, template, rules, landing registry endpoints |
| `hysteria/panel_services/` | framework-independent orchestration extracted from the legacy handler |
| `tests/test_panel_api_*.py` | route, schema, auth, read, mutation, and security contracts |

### Task 1: JSON response and route primitives

**Files:**
- Create: `hysteria/panel_api/__init__.py`
- Create: `hysteria/panel_api/router.py`
- Create: `hysteria/panel_api/responses.py`
- Create: `tests/test_panel_api_router.py`

**Interfaces:**
- Produces: `ApiRouter.add(method, template, handler)`, `ApiRouter.match(method, path)`, `ApiResponse`, `success(data, *, request_id, generated_at, status=200, headers=())`, `failure(status, code, message, *, fields, retryable, request_id, headers=())`

- [ ] **Step 1: Write failing route and envelope tests**

```python
def test_router_extracts_one_safe_path_parameter():
    router = ApiRouter()
    router.add("GET", "/api/v1/admin/users/:user", lambda **kw: kw)
    handler, params = router.match("GET", "/api/v1/admin/users/alice")
    assert params == {"user": "alice"}
    assert handler(**params) == {"user": "alice"}

def test_failure_envelope_has_no_legacy_reason_shape():
    response = failure(401, "login_required", "请重新登录", request_id="r1")
    assert response.status == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert response.body == {
        "error": {"code": "login_required", "message": "请重新登录",
                  "fields": {}, "retryable": False},
        "meta": {"request_id": "r1"},
    }
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run: `pytest tests/test_panel_api_router.py -v`

Expected: collection fails because `panel_api.router` and `panel_api.responses` do not exist.

- [ ] **Step 3: Implement strict routing and envelopes**

```python
class ApiRouter:
    def __init__(self):
        self._routes = []

    def add(self, method, template, handler):
        names = []
        parts = []
        for part in template.strip("/").split("/"):
            if part.startswith(":"):
                names.append(part[1:])
                parts.append(r"([A-Za-z0-9_.@+-]{1,128})")
            else:
                parts.append(re.escape(part))
        self._routes.append((method.upper(), re.compile(r"^/" + "/".join(parts) + r"$"), names, handler))

    def match(self, method, path):
        for expected, pattern, names, handler in self._routes:
            match = pattern.fullmatch(path)
            if expected == method.upper() and match:
                return handler, dict(zip(names, match.groups()))
        return None, {}
```

`responses.py` uses one immutable response type; byte serialization stays in the HTTP adapter so tests can validate envelopes without sockets.

```python
@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict
    headers: Mapping[str, str]

def failure(status, code, message, *, fields=None, retryable=False,
            request_id, headers=()):
    response_headers = {"Cache-Control": "no-store", **dict(headers)}
    return ApiResponse(status, {
        "error": {"code": code, "message": message,
                  "fields": fields or {}, "retryable": bool(retryable)},
        "meta": {"request_id": request_id},
    }, response_headers)
```

`success` adds `generated_at` to `meta`. The HTTP adapter rejects CR/LF in every header name/value. Overload responses pass `Retry-After` through this typed header map.

- [ ] **Step 4: Run primitive tests and regression smoke**

Run: `pytest tests/test_panel_api_router.py tests/test_http_utils.py tests/test_server_backpressure.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the primitives**

```bash
git add hysteria/panel_api tests/test_panel_api_router.py
git commit -m "feat(api): add panel API routing primitives"
```

### Task 2: Session bootstrap, role guards, and CSRF

**Files:**
- Create: `hysteria/panel_api/auth.py`
- Create: `hysteria/panel_api/session.py`
- Create: `hysteria/panel_services/sessions.py`
- Create: `tests/test_panel_api_auth.py`
- Modify: `hysteria/subscription_service.py:1626-1808,2516-2626`

**Interfaces:**
- Consumes: response helpers from Task 1
- Produces: `SessionView(role, identity, credential_kind, csrf_token)`, `resolve_session(handler)`, `require_role(view, role)`, `verify_csrf(handler, view)`, `session_routes(router)`

- [ ] **Step 1: Add failing auth and CSRF tests**

```python
def test_csrf_token_is_bound_to_session_and_server_secret():
    one = csrf_token("sid-one", b"s" * 32)
    assert one == csrf_token("sid-one", b"s" * 32)
    assert one != csrf_token("sid-two", b"s" * 32)

def test_subscription_session_cannot_change_panel_password(api_client):
    client = api_client.user_session("alice", kind="subscription_token")
    response = client.post("/api/v1/user/password", {"old_password": "x", "new_password": "y"})
    assert response.status == 403
    assert response.json["error"]["code"] == "password_session_required"
```

- [ ] **Step 2: Run the focused tests and observe failure**

Run: `pytest tests/test_panel_api_auth.py -v`

Expected: collection fails on missing `panel_api.auth`.

- [ ] **Step 3: Extract session operations and implement HMAC CSRF**

```python
@dataclass(frozen=True)
class SessionView:
    role: str
    identity: str = ""
    credential_kind: str = ""
    session_id: str = ""
    csrf_token: str = ""

def csrf_token(session_id: str, secret: bytes) -> str:
    digest = hmac.new(secret, ("hy2-panel-csrf-v1:" + session_id).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
```

Add `csrf_secret` generation to metadata initialization as a 32-byte URL-safe secret. `verify_csrf` uses `hmac.compare_digest`, requires `X-Hy2-CSRF`, and runs only after `http_utils.validate_same_origin_request` succeeds. Login keeps same-origin, bounded-parser, generic-failure, and rate-limit checks without an authenticated CSRF requirement.

- [ ] **Step 4: Add `/api/v1/session`, login, and logout dispatch**

```python
def get_session(request):
    view = resolve_session(request.handler)
    return success({
        "role": view.role,
        "identity": view.identity,
        "credential_kind": view.credential_kind,
        "csrf_token": view.csrf_token,
    }, request_id=request.request_id, generated_at=request.now_iso)
```

Login accepts `{role, username, password}` JSON, returns only the safe session view, and sets the existing cookie through the adapter. Logout deletes the current session and clears its cookie.

- [ ] **Step 5: Run auth, session, and legacy security tests**

Run: `pytest tests/test_panel_api_auth.py tests/test_subscription_security_regressions.py tests/test_login_failures_bounded.py tests/test_new_features.py -v`

Expected: all pass; legacy session behavior remains unchanged.

- [ ] **Step 6: Commit session API**

```bash
git add hysteria/panel_api hysteria/panel_services hysteria/subscription_service.py tests/test_panel_api_auth.py
git commit -m "feat(api): add session bootstrap and CSRF guards"
```

### Task 3: Wire `/api/v1` into the bounded HTTP handler

**Files:**
- Create: `hysteria/panel_api/app.py`
- Create: `tests/test_panel_api_http.py`
- Modify: `hysteria/subscription_service.py:6434-6674,7340-7437`

**Interfaces:**
- Consumes: `ApiRouter`, envelopes, session routes
- Produces: `create_api_app(dependencies)`, `PanelApi.dispatch(handler, *, send_body=True) -> bool`

- [ ] **Step 1: Add real-server HTTP tests**

```python
def test_unknown_api_route_is_json_404(panel_server):
    response = panel_server.get("/api/v1/not-real")
    assert response.status == 404
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json["error"]["code"] == "not_found"

def test_head_never_creates_a_session(panel_server):
    response = panel_server.head("/api/v1/session")
    assert "Set-Cookie" not in response.headers
```

- [ ] **Step 2: Run and confirm the routes do not exist**

Run: `pytest tests/test_panel_api_http.py -v`

Expected: `/api/v1/session` currently returns the legacy 404 body.

- [ ] **Step 3: Implement the adapter before legacy route dispatch**

```python
def handle_get(self, send_payload=True):
    if self.path == "/api/v1" or self.path.startswith("/api/v1/"):
        if PANEL_API.dispatch(self, send_body=send_payload):
            return
    # existing legacy GET routing follows

def do_POST(self):
    if self.path == "/api/v1" or self.path.startswith("/api/v1/"):
        PANEL_API.dispatch(self, send_body=True)
        return
    # existing legacy POST routing follows
```

The adapter parses a bounded JSON body only for API requests, rejects unsupported content types, emits UTF-8 JSON bytes through `send_response_body`, and never falls from an `/api/v1` miss into an HTML route.

- [ ] **Step 4: Run HTTP/backpressure/security tests**

Run: `pytest tests/test_panel_api_http.py tests/test_server_backpressure.py tests/test_subscription_security_regressions.py -v`

Expected: all pass.

- [ ] **Step 5: Commit HTTP integration**

```bash
git add hysteria/panel_api/app.py hysteria/subscription_service.py tests/test_panel_api_http.py
git commit -m "feat(api): mount versioned panel API"
```

### Task 4: Admin overview and user read models

**Files:**
- Create: `hysteria/panel_api/admin_users.py`
- Create: `hysteria/panel_api/schemas.py`
- Create: `hysteria/panel_services/users.py`
- Create: `tests/test_panel_api_admin_users.py`
- Modify: `hysteria/subscription_service.py:4587-4953`

**Interfaces:**
- Produces: `build_admin_overview(now)`, `build_admin_user(username, now)`, `serialize_user_summary`, `serialize_user_detail`

- [ ] **Step 1: Freeze safe overview/detail schemas in tests**

```python
def test_overview_never_contains_credentials(api_client, seeded_user):
    payload = api_client.admin().get("/api/v1/admin/overview").json["data"]
    encoded = json.dumps(payload)
    assert set(payload) == {"summary", "users", "reload", "generated_at"}
    assert "sub_token" not in encoded
    assert "password_hash" not in encoded
    assert "vless_uuid" not in encoded
    assert payload["users"][0]["revision"] == seeded_user["revision"]
```

- [ ] **Step 2: Run the new schema tests and observe 404**

Run: `pytest tests/test_panel_api_admin_users.py -v`

Expected: admin read endpoints return `not_found`.

- [ ] **Step 3: Move overview construction behind a service**

```python
SAFE_USER_FIELDS = (
    "username", "guest", "disabled", "expires_at", "quota_bytes",
    "extra_quota_bytes", "used_bytes", "online", "max_devices",
    "note", "tuic_enabled", "revision", "reload_pending",
)

def serialize_user_summary(model):
    return {name: model.get(name) for name in SAFE_USER_FIELDS}
```

Reuse current billing, usage, online, and reload helpers. Keep secrets in service-local data only long enough to calculate safe links for credential-generating mutations; reads never serialize them.

- [ ] **Step 4: Register read routes and auth guards**

```python
router.add("GET", "/api/v1/admin/overview", get_overview)
router.add("GET", "/api/v1/admin/users/:user", get_user)
```

Unknown users return the safe JSON `404`; unauthorized calls return JSON `401` without an HTML redirect.

- [ ] **Step 5: Run API and legacy overview tests**

Run: `pytest tests/test_panel_api_admin_users.py tests/test_admin_mutations_ajax.py tests/test_usage_page.py tests/test_product_ux_regressions.py -v`

Expected: all pass.

- [ ] **Step 6: Commit read models**

```bash
git add hysteria/panel_api hysteria/panel_services/users.py hysteria/subscription_service.py tests/test_panel_api_admin_users.py
git commit -m "feat(api): add safe admin user read models"
```

### Task 5: Admin user mutations and user self-service

**Files:**
- Create: `hysteria/panel_api/user_panel.py`
- Create: `hysteria/panel_services/credentials.py`
- Create: `tests/test_panel_api_user_mutations.py`
- Modify: `hysteria/panel_api/admin_users.py`
- Modify: `hysteria/subscription_service.py:7458-9180`

**Interfaces:**
- Produces: create/update/delete/pause/resume/reset/rotate user handlers; `build_user_panel`; password, rotation, and egress handlers

- [ ] **Step 1: Add mutation matrix tests**

```python
@pytest.mark.parametrize("status,code", [
    (401, "login_required"),
    (404, "user_not_found"),
    (409, "user_revision_conflict"),
    (422, "validation_failed"),
])
def test_user_patch_failure_contract(api_scenario, status, code):
    response = api_scenario(status).patch("/api/v1/admin/users/alice", {"revision": "stale"})
    assert response.status == status
    assert response.json["error"]["code"] == code
```

Add explicit tests for secrets absent from validation/conflict drafts, unlimited value `0`, invalid device range rejection, idempotent self-rotation, committed-but-reload-pending responses, password-session-only operations, and landing node authorization.

- [ ] **Step 2: Run tests and confirm mutation routes are absent**

Run: `pytest tests/test_panel_api_user_mutations.py -v`

Expected: all endpoint cases fail with `not_found`.

- [ ] **Step 3: Extract mutation orchestration into services**

```python
@dataclass(frozen=True)
class MutationResult:
    resource: dict
    reload: dict
    committed: bool = True
    credential_result: dict | None = None

def update_user(command, *, repositories, integrations):
    with repositories.users.locked():
        current = repositories.users.require(command.username)
        require_revision(current, command.revision)
        updated = apply_safe_user_patch(current, command.fields)
        plan = integrations.static_access.build_plan(updated)
        repositories.users.save(updated)
    reload_state = integrations.static_access.apply(plan)
    return MutationResult(serialize_user_detail(updated), reload_state)
```

Credential rotation calls the existing rotation/recovery functions rather than reimplementing them. Process/network side effects stay outside file locks exactly as current tests require.

- [ ] **Step 4: Register admin and user routes**

Register the routes from specification sections 6.3 for users, password, rotation, pause/resume, reset, and landing selection. Use CSRF, role, credential-kind, revision, and idempotency guards before service invocation.

```python
router.add("PATCH", "/api/v1/admin/users/:user", patch_user)
router.add("POST", "/api/v1/user/credentials/rotate", rotate_self)
router.add("PUT", "/api/v1/user/landing-egress", select_landing_egress)
```

- [ ] **Step 5: Run mutation, concurrency, recovery, and landing suites**

Run: `pytest tests/test_panel_api_user_mutations.py tests/test_admin_mutations_ajax.py tests/test_meta_token_concurrency.py tests/test_rotation_recovery.py tests/test_landing_user_flow.py tests/test_toggle_reload_status.py -v`

Expected: all pass.

- [ ] **Step 6: Commit user mutations**

```bash
git add hysteria/panel_api hysteria/panel_services/credentials.py hysteria/subscription_service.py tests/test_panel_api_user_mutations.py
git commit -m "feat(api): expose user management mutations"
```

### Task 6: Usage and operational read APIs

**Files:**
- Create: `hysteria/panel_api/usage.py`
- Create: `hysteria/panel_api/operations.py`
- Create: `hysteria/panel_services/usage.py`
- Create: `tests/test_panel_api_usage_operations.py`
- Modify: `hysteria/usage_dashboard.py`
- Modify: `hysteria/health.py`
- Modify: `hysteria/codex_dashboard.py`
- Modify: `hysteria/incident_console.py`

**Interfaces:**
- Produces: `/usage`, `/usage/history`, `/health`, `/codex-quota`, `/incidents`, `/logs`, updater and reload JSON payloads

- [ ] **Step 1: Add schema and tiering tests**

```python
def test_usage_summary_excludes_expensive_series(api_client):
    data = api_client.admin().get("/api/v1/admin/usage?view=summary").json["data"]
    assert "summary" in data
    assert "hourly" not in data
    assert "heatmap" not in data

def test_health_has_stable_probe_ids(api_client):
    data = api_client.admin().get("/api/v1/admin/health").json["data"]
    assert len(data["probes"]) == 15
    assert all(set(probe) >= {"id", "status", "label", "detail"} for probe in data["probes"])
```

- [ ] **Step 2: Run the focused tests and observe missing routes**

Run: `pytest tests/test_panel_api_usage_operations.py -v`

Expected: endpoint requests return `404`.

- [ ] **Step 3: Separate data models from HTML/SVG renderers**

Move aggregation into pure functions returning dictionaries/lists. Existing legacy render functions call the same models until cutover.

```python
def build_usage_payload(*, view, now, repositories):
    payload = {"summary": build_usage_summary(now, repositories)}
    if view == "full":
        payload.update(build_usage_charts(now, repositories))
    return payload
```

Health and incident models contain stable semantic IDs and safe text only. Logs are bounded, scrubbed, and never return raw query strings or credentials.

- [ ] **Step 4: Register read and bounded action routes**

Register usage refresh/reset, updater check/apply, reload status, and alert test using existing service functions and role/CSRF guards.

- [ ] **Step 5: Run usage, health, Codex, incident, updater, and reload tests**

Run: `pytest tests/test_panel_api_usage_operations.py tests/test_usage_page.py tests/test_health_probes.py tests/test_codex_quota.py tests/test_hysteria_update_web.py tests/test_toggle_reload_status.py tests/test_alert_integration.py -v`

Expected: all pass.

- [ ] **Step 6: Commit operational APIs**

```bash
git add hysteria/panel_api hysteria/panel_services/usage.py hysteria/usage_dashboard.py hysteria/health.py hysteria/codex_dashboard.py hysteria/incident_console.py tests/test_panel_api_usage_operations.py
git commit -m "feat(api): expose usage and operations data"
```

### Task 7: Settings, templates, rules, costs, and landing registry APIs

**Files:**
- Create: `hysteria/panel_api/configuration.py`
- Create: `hysteria/panel_services/configuration.py`
- Create: `tests/test_panel_api_configuration.py`
- Modify: `hysteria/subscription_service.py:5509-6360,7887-8182,8669-9452`

**Interfaces:**
- Produces: settings/cycle/cost/template/rule/landing read and mutation handlers with exact revision contracts

- [ ] **Step 1: Add corruption, conflict, and credential-redaction tests**

```python
def test_corrupt_template_is_visible_but_not_writable(api_client, corrupt_template):
    response = api_client.admin().get("/api/v1/admin/template")
    assert response.status == 200
    assert response.json["data"]["writable"] is False
    assert response.json["data"]["raw"] == corrupt_template

def test_stale_rule_delete_returns_conflict_without_deleting(api_client):
    response = api_client.admin().delete("/api/v1/admin/rules/r1", {"revision": "0" * 64})
    assert response.status == 409
    assert response.json["error"]["code"] == "template_revision_conflict"
```

Also test landing probes never serialize SOCKS credentials, cost bounds remain `0.1`–`20.0`, admin password rotation replaces sessions, and rule-pack user scope validates the selected user.

- [ ] **Step 2: Run the new tests and observe missing endpoints**

Run: `pytest tests/test_panel_api_configuration.py -v`

Expected: endpoint requests return `404`.

- [ ] **Step 3: Extract safe configuration models and commands**

```python
@dataclass(frozen=True)
class VersionedDocument:
    revision: str
    raw: str
    parsed: object | None
    writable: bool
    error: str = ""

def save_template(command, repository):
    with repository.locked():
        current = repository.read_versioned()
        require_exact_revision(current.revision, command.revision)
        parsed = validate_template(command.raw)
        repository.atomic_replace(command.raw)
    return VersionedDocument(repository.revision(), command.raw, parsed, True)
```

Landing registry serializers expose IDs, labels, host/port, health, assignments, and revisions but never username/password credentials.

- [ ] **Step 4: Register all specification configuration routes**

Register settings, cycle, cost calibration, password, alert test, landing registry/access, template, rules, and rule packs. Mutations use typed validation, CSRF, role checks, revisions, and safe field errors.

- [ ] **Step 5: Run configuration and preservation suites**

Run: `pytest tests/test_panel_api_configuration.py tests/test_operator_concurrency_regressions.py tests/test_template_lock.py tests/test_clash_template.py tests/test_landing_user_flow.py tests/test_product_ux_regressions.py -v`

Expected: all pass, including the operator's current Clash template changes.

- [ ] **Step 6: Commit configuration APIs**

```bash
git add hysteria/panel_api/configuration.py hysteria/panel_services/configuration.py hysteria/subscription_service.py tests/test_panel_api_configuration.py
git commit -m "feat(api): expose versioned panel configuration"
```

### Task 8: API contract completeness and backend module split

**Files:**
- Create: `hysteria/panel_repositories/__init__.py`
- Create: `hysteria/panel_repositories/paths.py`
- Create: `hysteria/panel_repositories/users.py`
- Create: `hysteria/panel_repositories/metadata.py`
- Create: `hysteria/panel_repositories/usage.py`
- Create: `hysteria/panel_repositories/templates.py`
- Create: `tests/test_panel_api_contract.py`
- Modify: `hysteria/subscription_service.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Produces: injectable `PanelPaths`, repository classes, complete route manifest, documented API boundary

- [ ] **Step 1: Add a route-manifest completeness test**

```python
EXPECTED = {
    ("GET", "/api/v1/session"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/user/password"),
    ("GET", "/api/v1/user/panel"),
    ("POST", "/api/v1/user/credentials/rotate"),
    ("PUT", "/api/v1/user/landing-egress"),
    ("GET", "/api/v1/admin/overview"),
    ("GET", "/api/v1/admin/users/:user"),
    ("POST", "/api/v1/admin/users"),
    ("PATCH", "/api/v1/admin/users/:user"),
    ("DELETE", "/api/v1/admin/users/:user"),
    ("POST", "/api/v1/admin/users/:user/pause"),
    ("POST", "/api/v1/admin/users/:user/resume"),
    ("POST", "/api/v1/admin/users/:user/credentials/rotate"),
    ("POST", "/api/v1/admin/users/:user/usage/reset"),
    ("PUT", "/api/v1/admin/users/:user/landing-egress-access"),
    ("GET", "/api/v1/admin/usage"),
    ("GET", "/api/v1/admin/usage/history"),
    ("POST", "/api/v1/admin/usage/refresh"),
    ("POST", "/api/v1/admin/usage/reset"),
    ("GET", "/api/v1/admin/health"),
    ("GET", "/api/v1/admin/codex-quota"),
    ("GET", "/api/v1/admin/incidents"),
    ("GET", "/api/v1/admin/logs"),
    ("GET", "/api/v1/admin/settings"),
    ("PATCH", "/api/v1/admin/settings"),
    ("GET", "/api/v1/admin/cycle"),
    ("PATCH", "/api/v1/admin/cycle"),
    ("POST", "/api/v1/admin/password"),
    ("POST", "/api/v1/admin/alerts/test"),
    ("GET", "/api/v1/admin/cost-calibration"),
    ("POST", "/api/v1/admin/cost-calibration/apply"),
    ("POST", "/api/v1/admin/cost-calibration/auto"),
    ("GET", "/api/v1/admin/landing-egresses"),
    ("POST", "/api/v1/admin/landing-egresses"),
    ("PATCH", "/api/v1/admin/landing-egresses/:id"),
    ("DELETE", "/api/v1/admin/landing-egresses/:id"),
    ("POST", "/api/v1/admin/landing-egresses/:id/check"),
    ("GET", "/api/v1/admin/template"),
    ("PUT", "/api/v1/admin/template"),
    ("GET", "/api/v1/admin/rules"),
    ("POST", "/api/v1/admin/rules"),
    ("PUT", "/api/v1/admin/rules/raw"),
    ("DELETE", "/api/v1/admin/rules/:id"),
    ("POST", "/api/v1/admin/rule-packs/:id/apply"),
    ("GET", "/api/v1/admin/hysteria-update"),
    ("POST", "/api/v1/admin/hysteria-update/check"),
    ("POST", "/api/v1/admin/hysteria-update/apply"),
    ("GET", "/api/v1/admin/reload-status"),
}

def test_required_api_route_families_are_registered(api_app):
    assert EXPECTED <= set(api_app.route_manifest())
```

- [ ] **Step 2: Run completeness and import-cycle tests**

Run: `pytest tests/test_panel_api_contract.py -v`

Expected: failure until the full manifest and injectable repositories exist.

- [ ] **Step 3: Move direct state access behind repositories**

```python
@dataclass(frozen=True)
class PanelPaths:
    users: Path
    metadata: Path
    usage: Path
    usage_daily: Path
    usage_hourly: Path
    online: Path
    template: Path
    state_dir: Path

class UsersRepository:
    def __init__(self, paths: PanelPaths):
        self.paths = paths

    def load_required(self):
        return state_store.load_json_strict(self.paths.users, {}, required=True)
```

Legacy globals construct default paths; tests construct temporary paths. Remove duplicated JSON read/write code from `subscription_service.py`, but leave render functions calling services until frontend cutover.

- [ ] **Step 4: Run the full Python suite**

Run: `pytest -q`

Expected: all tests pass; no legacy endpoint has regressed.

- [ ] **Step 5: Document dual-run API architecture**

Add an API v1 development section, response/error contract, session/CSRF behavior, and note that the legacy renderer remains temporary until the React cutover plan.

- [ ] **Step 6: Run preflight and diff checks**

Run: `./scripts/hy2-preflight.sh`

Expected: shell/Python checks and pytest pass.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 7: Commit the API phase**

```bash
git add hysteria/panel_api hysteria/panel_services hysteria/panel_repositories hysteria/subscription_service.py tests/test_panel_api_*.py README.md README.zh-CN.md
git commit -m "refactor(api): complete versioned panel backend"
```

## Phase Acceptance

- Every specification API route is registered and schema-tested.
- Legacy HTML/form routes and the new API return equivalent business outcomes against the same services.
- Full pytest and preflight pass.
- No general read payload contains credentials.
- CSRF, same-origin, session role, revisions, rotation recovery, and fail-closed tests pass.
- This phase is deployable without routing production users to React yet.
