# React 全前端迁移实施计划

> 状态：已实施（2026-09-16）。以下步骤是迁移历史记录；现行维护请参阅
> `frontend/README.md` 与 `deploy.sh`。计划中保留 legacy HTML/CSS 的条目已由
> 后续阶段完成并不再适用于生产部署。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Vite 构建的 React 资源独立提供所有网页页面，同时保持订阅、面板交换、CSV、证据下载及后端 API 合同不变。

**Architecture:** `frontend/src/styles/manifest.json` 是 CSS 顺序来源；React 通过 `frontend/src/styles/index.css` 导入这些源并由 Vite 输出 hash CSS。FastAPI 提供 React 文档壳和 `/static/react/assets`，兼容服务仅提供 `/sub/*`、`/panel/*`、CSV、证据及登记的 JSON/API。

**Tech Stack:** React 19.3, TypeScript 7.0, Vite 8.3, FastAPI/Uvicorn, Python pytest, Playwright browser checks.

**Spec:** `docs/superpowers/specs/2026-09-15-react-full-frontend-migration-design.md`

## Global Constraints

- 不修改 provider、模型、权限、证书、域名、443/9444 或代理配置。
- 不删除订阅、面板交换、CSV、证据下载、模板存储或后端领域服务。
- React 文档路由必须显式白名单；未知路径不得 SPA fallback。
- 旧 `hysteria/admin.css` 与 legacy 页面脚本已删除；8081 不再提供浏览器文档。
- 每个实现任务遵循 RED → GREEN → REFACTOR，并只提交该任务相关文件。

---

### Task 1: 锁定 React 资源边界

**Files:**
- Create: `tests/test_react_asset_boundary.py`
- Modify: `tests/test_react_preview.py:80-96`（更新既有页面资源断言）
- Modify: `tests/test_web_api_documents.py:1-120`（增加带 CSS manifest 的文档壳断言）

**Interfaces:**
- Consumes: `frontend/dist/index.html`, `hysteria/web_api/document_routes.py` 的 React 文档渲染。
- Produces: 可失败的门禁，要求 React HTML 只引用 `/static/react/assets/` 下的 hash CSS/JS，不出现 `/static/style.css`、`/static/*.js` 或 legacy `script_tag`。

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_react_entry_owns_css_and_has_no_legacy_asset_reference():
    html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
    assert '/static/style.css' not in html
    assert '/static/react/assets/' not in html  # Vite injects the final hashed links.


def test_built_react_document_contains_hashed_css():
    html = (ROOT / 'frontend/dist/index.html').read_text(encoding='utf-8')
    assert '/static/react/assets/' in html
    assert '.css' in html
    assert '/static/style.css' not in html
    assert '/static/shell.js' not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_react_asset_boundary.py`

Expected: FAIL because `frontend/index.html` still contains the external `/static/style.css` link and the current Vite output contains no CSS asset.

- [ ] **Step 3: Do not implement yet; record the failure**

Keep the failure focused on ownership. Do not weaken assertions to accept the old stylesheet.

- [ ] **Step 4: Run the existing route tests**

Run: `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_react_route_parity.py tests/test_react_server_entrypoint.py`

Expected: baseline passes; this confirms the new gate is the only red behavior.

- [ ] **Step 5: Commit the red test only**

```bash
git add tests/test_react_asset_boundary.py tests/test_react_preview.py tests/test_web_api_documents.py
git commit -m "test: require React-owned document assets"
```

### Task 2: Move CSS ownership into the Vite bundle

**Files:**
- Create: `frontend/src/styles/index.css`
- Modify: `frontend/src/main.tsx:1-20`
- Modify: `frontend/index.html:1-20`
- Test: `tests/test_react_asset_boundary.py`

**Interfaces:**
- Consumes: ordered CSS names from `hysteria/styles/manifest.json`.
- Produces: Vite `frontend/dist/assets/index-<hash>.css` linked by the built entry; legacy `hysteria/admin.css` remains unchanged and continues serving old HTML.

- [ ] **Step 1: Write the failing source-manifest test**

```python
import json
from pathlib import Path


def test_react_css_entry_lists_every_manifest_section_in_order():
    root = Path(__file__).resolve().parents[1]
    names = json.loads((root / 'hysteria/styles/manifest.json').read_text())
    entry = (root / 'frontend/src/styles/index.css').read_text()
    positions = [entry.index(f'../../../hysteria/styles/{name}') for name in names]
    assert positions == sorted(positions)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest -q tests/test_react_asset_boundary.py::test_react_css_entry_lists_every_manifest_section_in_order`

Expected: FAIL because `frontend/src/styles/index.css` does not exist.

- [ ] **Step 3: Implement the smallest CSS entry and import**

Create `frontend/src/styles/index.css` with one `@import` per manifest item, preserving manifest order:

```css
@import '../../../hysteria/styles/01-public-entry.css';
/* repeat the same form for every name in manifest.json, in order */
```

Add `import './styles/index.css';` to `frontend/src/main.tsx`, and remove the `vite-ignore` `/static/style.css` link from `frontend/index.html`.

- [ ] **Step 4: Run the focused green checks**

Run: `npm run typecheck:react && npm run build:react && pytest -q tests/test_react_asset_boundary.py`

Expected: PASS; `frontend/dist/index.html` has a `/static/react/assets/*.css` link and no `/static/style.css`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles/index.css frontend/src/main.tsx frontend/index.html tests/test_react_asset_boundary.py
git commit -m "feat: bundle React styles with Vite"
```

### Task 3: Remove stylesheet rewriting from React document bootstrap

**Files:**
- Modify: `hysteria/web_api/document_routes.py:58-128,154-206`
- Modify: `tests/test_web_api_documents.py:20-100`
- Modify: `tests/test_react_navigation.py:45-65`

**Interfaces:**
- Consumes: Vite-built `index.html` with hashed CSS and JS links.
- Produces: `_render_document()` that only injects title, body class, host and password limit; it never requires or rewrites `/static/style.css`.

- [ ] **Step 1: Write the failing regression test**

```python
def test_react_document_keeps_vite_hashed_stylesheet(tmp_path):
    dist = _dist(tmp_path)
    (dist / 'index.html').write_text(
        '<title>old</title><link rel="stylesheet" href="/static/react/assets/app-123.css">'
        '<body class="has-shell"><div id="root" data-public-host=""></div>',
        encoding='utf-8',
    )
    with TestClient(create_app(StubDocumentServices(), react_dist=dist)) as client:
        response = client.get('/admin', headers={'Cookie': 'sid=admin'})
    assert response.status_code == 200
    assert 'href="/static/react/assets/app-123.css"' in response.text
    assert '/static/style.css' not in response.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_web_api_documents.py::test_react_document_keeps_vite_hashed_stylesheet`

Expected: FAIL because `_render_document()` currently requires the legacy stylesheet marker when `css_version` is provided.

- [ ] **Step 3: Implement the minimal bootstrap change**

Remove `_css_version()` and the `css_version` parameter/branch from `_render_document()`. Stop reading `BASE_CSS_ETAG` in the React document endpoints. Leave legacy `BASE_CSS_ETAG` and `/static/style.css` handling in `subscription_service.py` untouched.

- [ ] **Step 4: Run focused and route tests**

Run: `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_web_api_documents.py tests/test_react_route_parity.py tests/test_react_navigation.py`

Expected: PASS after updating only assertions that explicitly describe the old React stylesheet rewrite.

- [ ] **Step 5: Commit**

```bash
git add hysteria/web_api/document_routes.py tests/test_web_api_documents.py tests/test_react_navigation.py
git commit -m "refactor: keep Vite stylesheet links in React documents"
```

### Task 4: Prove legacy compatibility remains isolated

**Files:**
- Create: `tests/test_react_legacy_boundary.py`
- Modify: `tests/test_react_cutover_config.py:1-180` only if the new asset path needs an explicit assertion
- Modify: `frontend/README.md` to describe production React ownership and the retained backend compatibility routes

**Interfaces:**
- Consumes: Nginx cutover contract and legacy service handlers.
- Produces: static/runtime checks proving React pages request only React assets while `/sub/*`, `/panel/*`, CSV and evidence remain available through the compatibility service.

- [ ] **Step 1: Write the failing boundary test**

```python
def test_legacy_routes_are_not_reclassified_as_react_documents():
    from web_api.document_routes import REACT_DOCUMENTS

    assert '/sub/example' not in REACT_DOCUMENTS
    assert '/panel/example' not in REACT_DOCUMENTS
    assert '/admin/usage.csv' not in REACT_DOCUMENTS
    assert '/admin/incidents/evidence.json' not in REACT_DOCUMENTS
```

- [ ] **Step 2: Run it**

Run: `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_react_legacy_boundary.py`

Expected: PASS once the explicit boundary test is added; if it fails, stop and inspect route ownership before changing Nginx.

- [ ] **Step 3: Update only production ownership documentation**

Replace stale “preview/no production cutover” text in `frontend/README.md` with the current route table, asset paths, compatibility paths, and exact checks (`npm run check:react`, browser smoke). Do not claim legacy HTML has been deleted while compatibility routes remain.

- [ ] **Step 4: Run compatibility tests**

Run: `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_react_cutover_config.py tests/test_react_cutover_runtime.py tests/test_react_deploy_wiring.py tests/test_react_legacy_boundary.py`

Expected: PASS; Nginx still sends `/sub/*`, `/panel/*`, CSV and evidence to the legacy service.

- [ ] **Step 5: Commit**

```bash
git add tests/test_react_legacy_boundary.py frontend/README.md tests/test_react_cutover_config.py
git commit -m "test: preserve legacy compatibility route boundary"
```

### Task 5: Full verification and staged deployment

**Files:**
- Modify: none unless a test exposes a migration regression.
- Test: `tests/test_react_asset_boundary.py`, all React browser suites, backend route suites.

**Interfaces:**
- Consumes: all prior tasks and the existing release validator/deploy script.
- Produces: an immutable release containing hashed CSS/JS and a production smoke record with rollback pointer.

- [ ] **Step 1: Run the full frontend gate**

Run: `npm run check:frontend`

Expected: PASS, including CSS source consistency, TypeScript, Vite build, React browser checks, and existing frontend regression gates.

- [ ] **Step 2: Validate release contents**

Run: `python3 scripts/hy2_panel_release.py validate frontend/dist`

Expected: PASS; the manifest and file list include at least one `.css` under `assets/`, and no source maps, symlinks or forbidden files.

- [ ] **Step 3: Run backend and route checks**

Run: `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_react_route_parity.py tests/test_react_server_entrypoint.py tests/test_react_cutover_config.py tests/test_react_cutover_runtime.py tests/test_react_deploy_wiring.py tests/test_web_api_documents.py`

Expected: PASS with no changes to subscription/download contracts.

- [ ] **Step 4: Stage and deploy one release**

Use the existing authorized deployment flow (`deploy.sh` with React enabled). Confirm the release pointer changes atomically and the legacy service remains active. Do not edit Nginx manually or touch certificates.

- [ ] **Step 5: Run production smoke checks**

Verify:

```text
GET /                     -> 200, HTML links only /static/react/assets/*
GET /login                -> 200, same asset boundary
GET /admin                -> 303 /login when anonymous
GET /user/panel           -> 303 /login when anonymous
GET /sub/<valid-user>     -> existing auth/download contract
GET /panel/<valid-user>   -> existing exchange contract
```

Also inspect browser network logs for `/static/style.css`, `/static/shell.js`, `/static/ui-core.js`, `/static/admin-poll.js`, `/static/usage.js` requests from React documents; none may occur.

- [ ] **Step 6: Commit verification metadata and stop**

Record release ID, smoke output, and rollback pointer in the existing deployment record. Do not claim completion until the browser and compatibility checks above are green.
