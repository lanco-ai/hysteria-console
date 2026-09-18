# ComfyUI 风格视频工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 React/FastAPI 工作台中增加管理员专用 `/admin/video`，以 React Flow 组织可保存、可恢复、可验证的图片/视频 DAG 工作流，并通过当前 Grok2API 供应商生成媒体。

**Architecture:** FastAPI 负责供应商适配、凭据、任务持久化、DAG 校验和状态轮询；React 负责 React Flow 画布、节点参数、预览和任务状态。浏览器只访问 `/api/video/*`，第三方请求只从 8083 发出。首尾帧、取消和合成能力以供应商运行时 capabilities 为准，未验证时不显示可用。

**Tech Stack:** React 19.3.0、TypeScript 7.0.2、Vite 8.3.0、普通 CSS、`@xyflow/react`（按路由动态加载）、FastAPI 0.141.1、Uvicorn、httpx 0.28.1、文件锁 + 原子 JSON 状态。

**Spec:** `docs/superpowers/specs/2026-09-18-video-workflow-design.md`

## Global Constraints

- 保持 React 19.3.0、React DOM 19.3.0、TypeScript 7.0.2、Vite 8.3.0、普通 CSS。
- 保留自研 `window.history.pushState`/`popstate` 路由，不引入 React Router、Vue、Next.js、Tailwind、Node 后端或第二个 FastAPI/Uvicorn。
- FastAPI/Uvicorn 继续监听 `127.0.0.1:8083`，生产链路继续为 Nginx 443/9444 → 8083。
- 不修改或提交现有 Agent 未提交文件；不修改 `docs/superpowers/specs/2026-09-12-http-cutover-notes.md` 或 `.codex-guard/`。
- API Key 只保存在服务端 0600 状态文件，不进入浏览器、localStorage、日志、工作流 JSON、错误响应或提交记录。
- 所有视频接口要求管理员 Session；写请求执行 same-origin/CSRF 校验。
- 每个行为先写失败测试，再实现最小代码并运行测试；不得用删除测试消除失败。

---

### Task 1: 供应商模型、能力探测和错误分类

**Files:**
- Create: `hysteria/web_api/video_models.py`
- Create: `hysteria/web_api/video_provider.py`
- Create: `tests/test_video_provider.py`

**Interfaces:**
- Produces `VideoSettings`, `Capability`, `Capabilities`, `ProviderJob`, `ProviderJobStatus`, `ProviderError` and `GrokVideoProvider`.
- `GrokVideoProvider.capabilities(settings) -> Capabilities` only advertises capabilities confirmed by the provider response.
- `GrokVideoProvider.generate_image(request) -> ProviderJob` calls `/v1/images/generations`.
- `GrokVideoProvider.generate_video(request) -> ProviderJob` calls `/v1/videos/generations`.
- `GrokVideoProvider.get_job(provider_job_id) -> ProviderJobStatus` calls `/v1/videos/{id}`.
- `GrokVideoProvider.cancel_job(provider_job_id) -> CancelResult` reports `unsupported` unless the provider confirms a real cancellation endpoint.

- [ ] **Step 1: Write failing provider tests**

```python
def test_video_provider_builds_image_request_without_exposing_key():
    request = fake_http_capture()
    provider = GrokVideoProvider(opener=request.opener)
    job = provider.generate_image(ImageRequest(prompt="blue circle", model="grok-imagine-image"), settings)
    assert request.url == "https://provider.test/v1/images/generations"
    assert request.headers["Authorization"] == "Bearer secret"
    assert job.provider_job_id == "asset-1"

def test_video_provider_maps_quota_error_without_upstream_body():
    provider = GrokVideoProvider(opener=http_429_with_secret_body)
    with pytest.raises(ProviderError) as error:
        provider.generate_video(VideoRequest(prompt="test", model="grok-imagine-video"), settings)
    assert error.value.code == "rate_limited"
    assert "secret body" not in str(error.value)

def test_capabilities_hide_unverified_first_last_frame_and_merge():
    result = provider.capabilities(settings)
    assert result.image_models
    assert result.video_models
    assert result.first_last_frame.supported is False
    assert result.video_composition.supported is False
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-symbol failures**

Run: `pytest -q tests/test_video_provider.py`

Expected: FAIL because the provider types and adapter do not exist yet.

- [ ] **Step 3: Implement strict request/response adapters**

Normalize Base URL once so `/v1`, `/v1/`, `/v1/v1` and endpoint-suffixed values cannot duplicate path segments. Use bounded httpx/urllib timeouts, parse only required fields, and map 401/403/404/429/5xx/timeout/invalid JSON to fixed codes. Never retain response bodies in exceptions.

- [ ] **Step 4: Run the provider tests and add the real probe contract**

Run: `pytest -q tests/test_video_provider.py`

Expected: PASS, including a fixture for the already observed `grok-imagine-video` create → `GET /v1/videos/{id}` status `done` flow and a fixture for `grok-imagine-video-1.5` quota exhaustion.

- [ ] **Step 5: Commit the isolated adapter**

```bash
git add hysteria/web_api/video_models.py hysteria/web_api/video_provider.py tests/test_video_provider.py
git commit -m "feat(video): add provider adapter and capability contracts"
```

### Task 2: Secure settings and capabilities API

**Files:**
- Create: `hysteria/web_api/video_service.py`
- Create: `hysteria/web_api/video_routes.py`
- Modify: `hysteria/web_api/app.py`
- Test: `tests/test_video_routes.py`

**Interfaces:**
- `VideoSettingsStore(path).read()`, `.public()`, `.update(...)` use `/root/hysteria/state/video/settings.json` by default.
- `register_video_routes(app, services, dispatch, settings_store=None, provider_factory=None)` registers `/api/video/settings`, `/api/video/connection/test`, `/api/video/capabilities`.

- [ ] **Step 1: Write failing route/security tests**

```python
def test_video_settings_requires_admin_and_masks_key(tmp_path, monkeypatch):
    store = VideoSettingsStore(tmp_path / "settings.json")
    app = create_app(_Services(), video_settings_store=store)
    with TestClient(app) as client:
        assert client.get("/api/video/settings").status_code == 401
        response = client.put(
            "/api/video/settings",
            headers={"Cookie": "sid=admin", "Sec-Fetch-Site": "same-origin"},
            json={"base_url": "https://provider.test/v1", "api_key": "secret", "provider": "grok"},
        )
        assert response.status_code == 200
        assert "secret" not in response.text
        assert oct(store.path.stat().st_mode & 0o777) == "0o600"

def test_video_capabilities_are_sanitized(monkeypatch):
    response = admin_client.get("/api/video/capabilities")
    assert response.status_code == 200
    assert "Authorization" not in response.text
    assert "api_key" not in response.json()
```

- [ ] **Step 2: Run tests to confirm missing route registration and store failures**

Run: `pytest -q tests/test_video_routes.py`

Expected: FAIL because `create_app` and `register_video_routes` do not yet expose the video routes.

- [ ] **Step 3: Implement the store and route registration**

Reuse `state_store.file_lock` and `state_store.save_json`; preserve an existing key when a settings update omits `api_key`. Reject malformed base URLs, unsupported schemes and oversized fields. Add `register_video_routes` beside the existing chat/agent registration without changing other routes.

- [ ] **Step 4: Run the security tests**

Run: `pytest -q tests/test_video_routes.py`

Expected: PASS with anonymous 401, cross-site write 403, masked settings, 0600 file mode and sanitized provider errors.

- [ ] **Step 5: Commit**

```bash
git add hysteria/web_api/video_service.py hysteria/web_api/video_routes.py hysteria/web_api/app.py tests/test_video_routes.py
git commit -m "feat(video): add secure settings and capabilities API"
```

### Task 3: Workflow schema, DAG validation and persistence

**Files:**
- Modify: `hysteria/web_api/video_models.py`
- Modify: `hysteria/web_api/video_service.py`
- Modify: `hysteria/web_api/video_routes.py`
- Create: `tests/test_video_workflows.py`

**Interfaces:**
- `validate_workflow(nodes, edges) -> ValidatedWorkflow` rejects unknown node types, invalid ports, missing required inputs and cycles.
- `WorkflowStore(path).list()`, `.get(workflow_id)`, `.save(workflow)`, `.delete(workflow_id)` use atomic JSON + lock.
- Routes expose `GET/POST /api/video/workflows`, `GET/DELETE /api/video/workflows/{workflow_id}`.

- [ ] **Step 1: Write failing DAG tests**

```python
def test_validate_workflow_returns_topological_order():
    result = validate_workflow(
        nodes=[node("prompt", "prompt"), node("image", "text_to_image"), node("preview", "preview")],
        edges=[edge("prompt", "text", "image", "prompt"), edge("image", "image", "preview", "media")],
    )
    assert result.order == ["prompt", "image", "preview"]

def test_validate_workflow_rejects_cycles_and_missing_inputs():
    with pytest.raises(VideoValidationError, match="cycle"):
        validate_workflow(nodes=[node("a", "prompt"), node("b", "prompt")], edges=[edge("a", "text", "b", "text"), edge("b", "text", "a", "text")])
    with pytest.raises(VideoValidationError, match="prompt"):
        validate_workflow(nodes=[node("image", "text_to_image")], edges=[])
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest -q tests/test_video_workflows.py`

Expected: FAIL because schema validation and workflow storage are absent.

- [ ] **Step 3: Implement schema validation and storage**

Use an explicit node/port registry. Apply Kahn’s algorithm for cycle detection, enforce exactly one input for required ports, preserve unknown future fields only under a versioned `metadata` object, and reject oversized workflow JSON. Persist only user workflow data, never provider settings or keys.

- [ ] **Step 4: Run workflow tests**

Run: `pytest -q tests/test_video_workflows.py tests/test_video_routes.py`

Expected: PASS, including refresh/read-back and delete behavior.

- [ ] **Step 5: Commit**

```bash
git add hysteria/web_api/video_models.py hysteria/web_api/video_service.py hysteria/web_api/video_routes.py tests/test_video_workflows.py
git commit -m "feat(video): validate and persist workflow graphs"
```

### Task 4: Asset upload, protected media and templates

**Files:**
- Modify: `hysteria/web_api/video_service.py`
- Modify: `hysteria/web_api/video_routes.py`
- Create: `tests/test_video_assets.py`
- Create: `frontend/src/features/video/VideoTemplates.ts`

**Interfaces:**
- `AssetStore.save_upload(filename, content_type, body) -> AssetMetadata` validates MIME, size and path.
- `AssetStore.open(asset_id) -> BinaryIO` only after the route has authenticated the administrator.
- `VIDEO_TEMPLATES` contains three DAGs: prompt→image→video, first/last frame video, and two generated frames→first/last frame video; unavailable nodes remain marked unsupported.

- [ ] **Step 1: Write failing asset/security tests**

```python
def test_asset_store_rejects_path_traversal_and_unsupported_mime(tmp_path):
    store = AssetStore(tmp_path / "assets", max_bytes=1024)
    with pytest.raises(VideoValidationError):
        store.save_upload("../../secret", "text/plain", b"x")
    with pytest.raises(VideoValidationError):
        store.save_upload("x.exe", "application/octet-stream", b"x")

def test_asset_content_route_does_not_expose_files_to_anonymous(tmp_path):
    response = client.get("/api/video/assets/asset-1/content")
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to observe missing asset store and templates**

Run: `pytest -q tests/test_video_assets.py`

Expected: FAIL because upload validation and protected content routes do not exist.

- [ ] **Step 3: Implement bounded asset storage and protected routes**

Use a 0700 directory, generated asset IDs, MIME allowlist, per-file and total-size limits, atomic metadata writes and no user-controlled filesystem path. Proxy only known assets through `/api/video/assets/{id}/content`; do not return arbitrary local paths. Add the templates as data-only definitions.

- [ ] **Step 4: Run asset and route tests**

Run: `pytest -q tests/test_video_assets.py tests/test_video_workflows.py`

Expected: PASS, including binary content type and download disposition for authorized requests.

- [ ] **Step 5: Commit**

```bash
git add hysteria/web_api/video_service.py hysteria/web_api/video_routes.py tests/test_video_assets.py frontend/src/features/video/VideoTemplates.ts
git commit -m "feat(video): add protected assets and workflow templates"
```

### Task 5: Run orchestration, polling and recovery

**Files:**
- Modify: `hysteria/web_api/video_service.py`
- Modify: `hysteria/web_api/video_routes.py`
- Modify: `hysteria/web_api/app.py`
- Create: `tests/test_video_runs.py`

**Interfaces:**
- `RunService.submit(workflow_id, request_snapshot) -> RunRecord` validates and snapshots before any paid request.
- `RunService.get(run_id) -> RunRecord` and `.list()` expose only sanitized metadata.
- `RunService.cancel(run_id) -> RunRecord` returns `cancel_requested` or `cancel_unsupported` based on provider evidence.
- `RunService.resume_pending()` rehydrates only `queued`/`running` records after restart.
- Routes expose `POST/GET /api/video/runs`, `GET /api/video/runs/{run_id}`, `POST /api/video/runs/{run_id}/cancel`.

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_run_waits_for_image_before_submitting_video(fake_provider):
    run = service.submit(workflow_with_image_then_video)
    service.tick(run.id)
    assert fake_provider.image_calls == 1
    assert fake_provider.video_calls == 0
    fake_provider.complete_image()
    service.tick(run.id)
    assert fake_provider.video_calls == 1

def test_submit_timeout_does_not_duplicate_paid_request(fake_provider):
    run = service.submit(workflow_with_image)
    fake_provider.create_then_timeout = True
    service.tick(run.id)
    service.tick(run.id)
    assert fake_provider.image_calls == 1
    assert run.node_status["image"].state in {"running", "failed"}

def test_cancel_reports_unsupported_instead_of_faking_success(fake_provider):
    run = service.submit(workflow_with_image)
    result = service.cancel(run.id)
    assert result.status == "cancel_unsupported"
```

- [ ] **Step 2: Run tests and verify missing run service failures**

Run: `pytest -q tests/test_video_runs.py`

Expected: FAIL because run persistence, polling and recovery are absent.

- [ ] **Step 3: Implement bounded run execution**

Persist a complete workflow snapshot before provider submission. Execute one ready node at a time in topological order; use provider task IDs to poll, never resubmit after an ambiguous timeout, and cap polling interval/total duration. Save sanitized status after every transition. Keep the scheduler callable from startup without blocking FastAPI request handling; the initial low-concurrency implementation may run through the existing request dispatch plus a bounded poll request, then be upgraded to a background loop only if the current service lifecycle provides a safe hook.

- [ ] **Step 4: Run run/recovery tests and existing state tests**

Run: `pytest -q tests/test_video_runs.py tests/test_state_store.py tests/test_video_routes.py`

Expected: PASS; no API key or prompt appears in serialized records.

- [ ] **Step 5: Commit**

```bash
git add hysteria/web_api/video_service.py hysteria/web_api/video_routes.py hysteria/web_api/app.py tests/test_video_runs.py
git commit -m "feat(video): orchestrate persisted media runs"
```

### Task 6: React route, navigation and typed API client

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/shared/navigation.ts`
- Modify: `frontend/src/shared/icons.tsx`
- Create: `frontend/src/features/video/videoTypes.ts`
- Create: `frontend/src/features/video/videoApi.ts`
- Create: `tests/test_react_video_contract.py`

**Interfaces:**
- `VideoPage` receives the same `publicHost`/shell contract as existing admin pages.
- `videoApi.ts` exports `loadVideoSettings`, `saveVideoSettings`, `loadVideoCapabilities`, `loadWorkflows`, `saveWorkflow`, `createRun`, `loadRun`, `cancelRun`, `uploadAsset`.
- Parsers reject arrays, missing IDs and unexpected sensitive fields.

- [ ] **Step 1: Write failing route/contract tests**

```python
def test_admin_video_is_an_exact_react_document_route():
    assert "/admin/video" in document_routes.REACT_DOCUMENTS
    assert "/admin/video" in main_source()

def test_navigation_contains_ai_video_target():
    assert {item["href"] for item in navigation_items()} >= {"/admin/video"}
```

- [ ] **Step 2: Run contract tests and verify the route is absent**

Run: `pytest -q tests/test_react_video_contract.py`

Expected: FAIL because the route, icon and typed API client do not exist.

- [ ] **Step 3: Add exact route and safe API types**

Add `/admin/video` to `WORKBENCH`/admin route metadata and `document_routes.REACT_DOCUMENTS`, map it to the admin session guard, and add a navigation item. Keep `/chat` behavior unchanged. Use `fetch` with `credentials: 'same-origin'`, no API key fields in client state, and explicit response parsers.

- [ ] **Step 4: Run route and TypeScript checks**

Run: `pytest -q tests/test_react_video_contract.py && npm run typecheck:react`

Expected: route contract and TypeScript pass with a placeholder `VideoPage` import.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/main.tsx frontend/src/shared/navigation.ts frontend/src/shared/icons.tsx frontend/src/features/video/videoTypes.ts frontend/src/features/video/videoApi.ts tests/test_react_video_contract.py
git commit -m "feat(video): add authenticated route and API client"
```

### Task 7: Dynamic React Flow canvas and node execution UI

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Create: `frontend/src/features/video/VideoPage.tsx`
- Create: `frontend/src/features/video/VideoCanvas.tsx`
- Create: `frontend/src/features/video/VideoNode.tsx`
- Modify: `frontend/src/features/video/VideoTemplates.ts`
- Test: `tests/react_video_browser.cjs`

**Interfaces:**
- `VideoCanvas` accepts `nodes`, `edges`, `onNodesChange`, `onEdgesChange`, `onConnect`, `onSelect` and loads `@xyflow/react` only when mounted.
- `VideoPage` owns the selected node, workflow version, run state and drawer state; it never sends a provider request while editing.

- [ ] **Step 1: Write failing browser contract**

```js
test('video page exposes canvas, templates, run and settings controls', async ({ page }) => {
  await page.goto('/admin/video');
  await expect(page.getByRole('heading', { name: 'AI 视频' })).toBeVisible();
  await expect(page.getByRole('button', { name: '运行工作流' })).toBeVisible();
  await expect(page.getByText('提示词 → 文生图 → 图生视频')).toBeVisible();
});
```

- [ ] **Step 2: Run the browser contract and verify the missing page failure**

Run: `node tests/react_video_browser.cjs`

Expected: FAIL because `/admin/video` has no page component.

- [ ] **Step 3: Add and pin the React Flow dependency**

Install a registry-resolved `@xyflow/react` version compatible with React 19, commit the lockfile change, and keep its CSS imported only from the video chunk. Do not add a second router or UI framework.

- [ ] **Step 4: Implement canvas, node types and template loading**

Render only capabilities marked supported. Connect handles by declared type, reject cycles before saving, provide visible first/last handles only when the capability says supported, and make “运行工作流” call `createRun` only after client-side validation. Save/restore node positions and workflow JSON through `/api/video/workflows`.

- [ ] **Step 5: Run browser and build checks**

Run: `node tests/react_video_browser.cjs && npm run typecheck:react && npm run build:react`

Expected: PASS; Vite emits a separate video chunk and existing routes still build.

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json frontend/src/features/video frontend/src/main.tsx tests/react_video_browser.cjs
git commit -m "feat(video): add React Flow workflow canvas"
```

### Task 8: Settings drawer, task panel, preview and responsive CSS

**Files:**
- Create: `frontend/src/features/video/VideoSettingsDrawer.tsx`
- Create: `frontend/src/features/video/VideoRunPanel.tsx`
- Create: `frontend/src/features/video/VideoAssetPreview.tsx`
- Create: `frontend/src/styles/sections/21-video.css`
- Modify: `frontend/src/styles/index.css`
- Modify: `frontend/src/styles/manifest.json`
- Modify: `frontend/src/features/video/VideoPage.tsx`
- Test: `tests/test_video_ui_contract.py`

**Interfaces:**
- Settings drawer renders only masked key state and connection/capability results.
- Run panel polls `loadRun` with bounded backoff, shows `queued/running/succeeded/failed/cancel_unsupported` honestly, and preserves partial state on refresh.
- Preview chooses image/video rendering from asset metadata and uses the authenticated content route.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_video_css_uses_existing_tokens_and_mobile_drawers():
    css = Path("frontend/src/styles/sections/21-video.css").read_text()
    assert "env(safe-area-inset-bottom)" in css
    assert "100dvh" in css
    assert "--" in css

def test_video_settings_never_renders_raw_api_key():
    source = Path("frontend/src/features/video/VideoSettingsDrawer.tsx").read_text()
    assert "api_key_masked" in source
    assert "localStorage" not in source
```

- [ ] **Step 2: Run UI contract tests and verify missing-file failures**

Run: `pytest -q tests/test_video_ui_contract.py`

Expected: FAIL because the drawer, run panel, preview and CSS section do not exist.

- [ ] **Step 3: Implement drawer, run panel and preview**

Use the existing shell buttons, focus behavior and CSS tokens. Settings writes through `PUT /api/video/settings`; no raw key is put into React state beyond the transient input and it is cleared after save. The run panel never claims cancellation unless the API reports it.

- [ ] **Step 4: Implement responsive CSS and import order**

Add `21-video.css` after `20-agent.css`. Desktop uses fixed sidebar/property widths and a bounded canvas; iPad portrait and mobile switch panels to drawers, preserve `100dvh`, safe-area padding and keyboard-visible composer space, and prevent horizontal overflow.

- [ ] **Step 5: Run UI, type, lint and build checks**

Run: `pytest -q tests/test_video_ui_contract.py && npm run typecheck:react && npm run lint:css && npm run build:react`

Expected: PASS with no change to existing page CSS snapshots.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/video frontend/src/styles/sections/21-video.css frontend/src/styles/index.css frontend/src/styles/manifest.json tests/test_video_ui_contract.py
git commit -m "feat(video): add settings runs and responsive workspace"
```

### Task 9: Full regression, real provider probe and release preparation

**Files:**
- Create: `tests/test_video_regression.py`
- Modify: `docs/superpowers/specs/2026-09-18-video-workflow-design.md` only if runtime evidence changes a capability statement
- No changes to Agent files, protected spec, `.codex-guard/`, Nginx or systemd in this task

- [ ] **Step 1: Add regression assertions**

Cover anonymous `/admin/video` redirect, `/chat` remaining 404, API key absence from response/localStorage/build output, cycle rejection, and existing chat/overview route registration.

- [ ] **Step 2: Run focused and full checks**

```bash
pytest -q tests/test_video_*.py tests/test_react_video_*.py
npm run typecheck:react
npm run lint:js
npm run lint:css
npm run build:react
npm run test:react-assets
python3 -m pytest -q
git diff --check
```

- [ ] **Step 3: Run the real provider smoke probe without exposing secrets**

Use the server-side configured provider through the authenticated admin flow. Record only status, model ID, job state, and sanitized error code. Verify one image job, one `grok-imagine-video` job, and the capabilities response. Do not probe `grok-imagine-video-1.5` repeatedly after the observed quota exhaustion.

- [ ] **Step 4: Review the diff and preserve concurrent files**

Run `git status --short --branch`, `git diff --name-only origin/refactor/react-only-frontend...HEAD`, and verify the changed list contains only video work, its tests/docs and the dependency lockfile. If Agent files change concurrently, leave them unstaged and report them.

- [ ] **Step 5: Commit regression coverage**

```bash
git add tests/test_video_regression.py
git commit -m "test(video): lock workflow and provider boundaries"
```

### Task 10: Versioned deployment and production verification

**Files:**
- Modify only the generated frontend release directory through the existing deployment script.
- Do not modify Nginx/systemd unless a test proves a route registration issue.

- [ ] **Step 1: Create a UTC rollback backup**

Back up the current React release pointer/target and relevant FastAPI files under `/var/lib/hysteria/` using a new timestamped directory. Record the previous release target and Git HEAD; do not copy credentials into the report.

- [ ] **Step 2: Build and atomically publish**

Run the existing React release/deploy flow after all tests pass. Publish the new `frontend/dist` to a versioned release directory, switch `panel/current` atomically, run `nginx -t`, then reload only the React service/Nginx path required by the established process.

- [ ] **Step 3: Verify production routes and services**

Check `/admin/video` on 443 and 9444, anonymous redirect behavior, `/chat` 404, `hysteria-react.service` active, 8083 listening, and the existing overview/chat/subscription endpoints. Use an authenticated browser session only if already available; never read or print credentials.

- [ ] **Step 4: Record rollback command and final state**

Report the exact backup directory, previous release target, Git commits, test commands/results, production asset version, provider capability limitations and any unverified iPad Safari behavior. Keep all concurrent Agent changes outside the release commit.

---

## Plan self-review

- Coverage: provider probing, secure settings, capabilities, DAG validation, assets, persisted runs, route/navigation, React Flow canvas, responsive UI, regression and deployment each have a task.
- Placeholder scan: no `TBD`, `TODO`, “implement later” or unspecified test command remains; unsupported supplier capabilities have explicit runtime status and error codes.
- Interface consistency: `VideoSettingsStore`, `VideoProvider`, `WorkflowStore`, `RunService` and `videoApi.ts` names are reused consistently across tasks.
- Scope: no task touches the existing Agent changes or protected migration files; no new service, port, router framework or database is introduced.
