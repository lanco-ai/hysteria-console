# React Panel Atomic Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install the Vite application as an atomic recoverable release, route production UI traffic to it, remove the entire legacy frontend, and prove end-to-end rollback and completion.

**Architecture:** A validated immutable release directory is installed before service quiescence; deployment atomically switches a constrained `current` symlink inside the existing durable transaction. nginx serves SPA documents/assets while proxying API and backend-owned routes. After cutover verification, the Python renderer and legacy assets/tests are deleted.

**Tech Stack:** Node 24.20.0, Vite production build, Python release validator, nginx, existing Bash deployment transaction and Python recovery helper, systemd, pytest, Vitest, Playwright

**Spec:** `docs/superpowers/specs/2026-08-30-react-vite-panel-redesign.md`

## Global Constraints

- Node archives are pinned to `24.20.0`: linux x64 SHA-256 `2f2c0da162318f0de47665410c7c8c2ed3d36c8f3105de4bbc61176c70a7cbf2`; linux arm64 SHA-256 `5f4ddab610c1ab2016b3c227cebdbf6d9495161487e4739c7b90090595f465f7`.
- Build and validation finish before nginx, panel, auth, traffic, or proxy services are stopped.
- npm lifecycle scripts are disabled; no development server runs in production.
- Releases contain regular files only, no source maps, no unlisted suffixes, no writable-by-group/world files, and no path traversal.
- nginx must never SPA-fallback an API, asset, subscription, QR, evidence, CSV, or health path.
- Dynamic/API/SPA entry responses are `no-store`; hashed assets are immutable.
- Final CSP has no arbitrary inline script permission.
- Keep the active and immediately previous validated release until the deployment commits.
- Preserve unrelated operator modifications in the Clash template and its test.

---

## File Structure

| Path | Responsibility |
|---|---|
| `scripts/hy2-panel-release.py` | validate, hash, install, activate, inspect, and prune Vite releases |
| `scripts/hy2-build-panel.sh` | pinned Node acquisition and unprivileged reproducible build |
| `scripts/hy2-deploy-recovery.py` | explicit managed active-release pointer recovery |
| `nginx/hysteria-panel*.conf` | exact SPA/assets/backend route ownership and CSP |
| `deploy.sh` | prebuild, durable registration, activate, readiness, rollback, pruning |
| `scripts/hy2-health-check.sh` | frontend manifest/entry/asset health checks |
| `tests/test_panel_release.py` | release validator and activation safety |
| `tests/test_panel_nginx.py` | route containment, caching, CSP, token-log policy |
| `tests/test_deploy_durable_recovery.py` | pointer-switch fault injection |
| `tests/test_panel_cutover.py` | no-legacy and complete-route assertions |

### Task 1: Vite release validator and atomic installer

**Files:**
- Create: `scripts/hy2-panel-release.py`
- Create: `tests/test_panel_release.py`

**Interfaces:**
- Produces: `validate_dist(path) -> Release`, CLI `validate`, `install`, `activate`, `inspect`, `prune`

- [ ] **Step 1: Add failing unsafe-tree and atomic-activation tests**

```python
def test_rejects_symlink_and_source_map(tmp_path):
    dist = make_valid_dist(tmp_path)
    (dist / "assets" / "escape.js").symlink_to("/etc/passwd")
    with pytest.raises(ReleaseError, match="symlink"):
        validate_dist(dist)

def test_activate_replaces_only_managed_relative_link(tmp_path):
    root = make_release_root(tmp_path, ids=("old", "new"))
    activate_release(root, "new")
    assert os.readlink(root / "current") == "releases/new"
```

- [ ] **Step 2: Run and confirm the validator is absent**

Run: `pytest tests/test_panel_release.py -v`

Expected: collection fails on missing script module.

- [ ] **Step 3: Implement strict manifest validation**

```python
ALLOWED_SUFFIXES = {".html", ".js", ".css", ".json", ".svg", ".woff2", ".png", ".ico"}
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RELEASE_BYTES = 64 * 1024 * 1024

@dataclass(frozen=True)
class Release:
    release_id: str
    files: tuple[tuple[str, str, int], ...]
    entry: str

def validate_dist(path: Path) -> Release:
    root = path.resolve(strict=True)
    records = []
    total = 0
    for candidate in sorted(root.rglob("*")):
        st = candidate.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            if candidate.is_dir() and not candidate.is_symlink():
                continue
            raise ReleaseError("release contains a symlink or non-regular file")
        rel = candidate.relative_to(root).as_posix()
        if candidate.suffix not in ALLOWED_SUFFIXES or candidate.suffix == ".map":
            raise ReleaseError(f"unsupported release artifact: {rel}")
        if st.st_size > MAX_FILE_BYTES:
            raise ReleaseError(f"release artifact too large: {rel}")
        total += st.st_size
        records.append((rel, sha256_file(candidate), st.st_size))
    if total > MAX_RELEASE_BYTES or not (root / "index.html").is_file() or not (root / ".vite/manifest.json").is_file():
        raise ReleaseError("invalid or oversized Vite release")
    release_id = hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()[:24]
    return Release(release_id, tuple(records), "index.html")
```

Explicitly allow `.vite/manifest.json`; reject every other hidden path.

- [ ] **Step 4: Implement install, fsync, activation, inspect, and prune**

Copy into `releases/.staging-<id>`, set directories `0755` and files `0644`, verify every copied hash, fsync files/directories, then `os.replace` to `releases/<id>`. `activate` creates a sibling temporary relative symlink and atomically replaces `current` only after revalidating the target release. `prune` always preserves current, explicit previous, and newest extra release.

- [ ] **Step 5: Run release and security tests**

Run: `pytest tests/test_panel_release.py -v`

Expected: all pass, including injected copy failure leaving the old `current` link intact.

- [ ] **Step 6: Commit the release manager**

```bash
git add scripts/hy2-panel-release.py tests/test_panel_release.py
git commit -m "feat(deploy): add atomic panel release manager"
```

### Task 2: Reproducible unprivileged panel build

**Files:**
- Create: `scripts/hy2-build-panel.sh`
- Create: `tests/test_panel_build_script.py`
- Modify: `scripts/hy2-preflight.sh`

**Interfaces:**
- Produces: `hy2-build-panel.sh <repo> <output-dir>` and a validated production dist

- [ ] **Step 1: Add failing pinned-version/checksum tests**

```python
def test_build_script_pins_node_and_disables_lifecycle_scripts():
    text = SCRIPT.read_text()
    assert 'NODE_VERSION="24.20.0"' in text
    assert "2f2c0da162318f0de47665410c7c8c2ed3d36c8f3105de4bbc61176c70a7cbf2" in text
    assert "5f4ddab610c1ab2016b3c227cebdbf6d9495161487e4739c7b90090595f465f7" in text
    assert "npm ci --ignore-scripts" in text
    assert "systemctl" not in text
```

- [ ] **Step 2: Run and confirm script absence**

Run: `pytest tests/test_panel_build_script.py -v`

Expected: test fails because the script does not exist.

- [ ] **Step 3: Implement pinned Node acquisition**

Map `x86_64 -> x64` and `aarch64 -> arm64`, download `https://nodejs.org/dist/v24.20.0/node-v24.20.0-linux-$arch.tar.xz` to a `mktemp -d` directory, verify the exact constant with `sha256sum -c`, extract without privilege escalation, and reject any archive entry that is absolute or contains `..` before extraction.

- [ ] **Step 4: Build as an unprivileged identity**

Copy only `frontend/package*.json` first, run `npm ci --ignore-scripts --no-audit --no-fund`, then copy frontend source and run `npm run typecheck`, `npm run test`, `npm run build`, and `npm run check:bundle`. Use an isolated task-specific npm cache and umask `022`; never reuse `$HOME` or set it to a task path.

- [ ] **Step 5: Validate the produced dist and run tests**

Run: `pytest tests/test_panel_build_script.py -v`

Run: `scripts/hy2-build-panel.sh "$PWD" "$(mktemp -d)/dist"`

Expected: tests pass and the command produces a validator-approved release.

- [ ] **Step 6: Commit build integration**

```bash
git add scripts/hy2-build-panel.sh scripts/hy2-preflight.sh tests/test_panel_build_script.py
git commit -m "feat(deploy): build panel reproducibly"
```

### Task 3: Durable recovery for the managed release pointer

**Files:**
- Modify: `scripts/hy2-deploy-recovery.py`
- Modify: `tests/test_deploy_durable_recovery.py`
- Modify: `deploy.sh`

**Interfaces:**
- Produces: recovery manifest entry kind `managed_panel_release`, safe snapshot/restore of `/usr/local/share/hy2/panel/current`

- [ ] **Step 1: Add recovery fault tests before pointer switch**

```python
def test_recovery_restores_previous_validated_panel_release(recovery_env):
    recovery_env.activate("old")
    recovery_env.prepare_panel_pointer()
    recovery_env.activate("new")
    recovery_env.simulate_sigkill()
    recovery_env.recover()
    assert recovery_env.current_release() == "old"

def test_recovery_rejects_pointer_outside_release_root(recovery_env):
    recovery_env.current.symlink_to("/etc")
    with pytest.raises(RecoveryError):
        recovery_env.prepare_panel_pointer()
```

- [ ] **Step 2: Run focused fault tests and observe failure**

Run: `pytest tests/test_deploy_durable_recovery.py -k panel_release -v`

Expected: new recovery entry kind is unsupported.

- [ ] **Step 3: Implement explicit managed-link metadata**

```json
{
  "kind": "managed_panel_release",
  "path": "/usr/local/share/hy2/panel/current",
  "release_root": "/usr/local/share/hy2/panel/releases",
  "target": "releases/<24-hex-id>",
  "manifest_sha256": "<64-hex>"
}
```

Preparation accepts only a root-owned symlink with one relative target matching `releases/[0-9a-f]{24}`. It validates the target through `hy2-panel-release.py inspect`, stores the exact target and manifest hash, and never follows arbitrary symlinks. Recovery revalidates the stored release before atomically recreating the link.

- [ ] **Step 4: Register the pointer in deploy durable state**

Add a dedicated `add_managed_panel_release` path rather than passing the symlink through ordinary regular-file artifacts. Ensure the old target cannot be pruned before commit/recovery log deletion.

- [ ] **Step 5: Run complete recovery suites**

Run: `pytest tests/test_deploy_durable_recovery.py tests/test_deploy_security_regressions.py -v`

Expected: all pass.

- [ ] **Step 6: Commit recovery support**

```bash
git add scripts/hy2-deploy-recovery.py deploy.sh tests/test_deploy_durable_recovery.py
git commit -m "feat(deploy): recover panel release switches"
```

### Task 4: nginx SPA, assets, backend ownership, and strict CSP

**Files:**
- Modify: `nginx/hysteria-panel.conf`
- Modify: `nginx/hysteria-panel-https.conf`
- Modify: `nginx/hysteria-panel-bootstrap.conf`
- Create: `nginx/hysteria-panel-routes.conf`
- Create: `tests/test_panel_nginx.py`

**Interfaces:**
- Produces: exact nginx ownership for SPA documents, `/assets`, and backend routes

- [ ] **Step 1: Add failing route containment and header tests**

```python
def test_assets_never_fall_back_to_index():
    text = ROUTES.read_text()
    assert "location ^~ /assets/" in text
    assert "try_files $uri =404" in text

def test_final_csp_disallows_inline_scripts():
    for path in (HTTP_CONF, HTTPS_CONF):
        csp = extract_csp(path.read_text())
        assert "script-src 'self'" in csp
        assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
```

- [ ] **Step 2: Run and observe missing route include**

Run: `pytest tests/test_panel_nginx.py -v`

Expected: failures on missing route file and old CSP.

- [ ] **Step 3: Implement exact static and backend locations**

```nginx
location ^~ /assets/ {
    root /usr/local/share/hy2/panel/current;
    try_files $uri =404;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    add_header X-Content-Type-Options nosniff always;
}

location = /admin { try_files /index.html =503; add_header Cache-Control "no-store" always; }
location ~ ^/admin/(user/[^/]+|usage|health|codex|incidents|config|rules|settings|landing-egresses|logs)$ {
    try_files /index.html =503;
    add_header Cache-Control "no-store" always;
}
```

Add exact public/user routes. Place `/api/v1/`, `/sub/`, `/panel/`, CSV, evidence, QR, and health locations before UI regexes and proxy them with existing limits/timeouts/log policy.

- [ ] **Step 4: Remove inline-script CSP permission and validate nginx**

Use one shared route include from both HTTP and TLS vhosts. Preserve ACME behavior and HTTPS redirects. Run `nginx -t` against a temporary rendered configuration when nginx is installed.

- [ ] **Step 5: Run nginx/security suites**

Run: `pytest tests/test_panel_nginx.py tests/test_https_activation_transaction.py tests/test_deploy_security_regressions.py -v`

Expected: all pass.

- [ ] **Step 6: Commit nginx routing**

```bash
git add nginx tests/test_panel_nginx.py
git commit -m "feat(nginx): route React panel and immutable assets"
```

### Task 5: Deployment activation and frontend readiness

**Files:**
- Modify: `deploy.sh`
- Modify: `scripts/hy2-health-check.sh`
- Modify: `tests/test_deploy_durable_recovery.py`
- Modify: `tests/test_operational_health_readiness.py`
- Create: `tests/test_panel_deploy_integration.py`

**Interfaces:**
- Produces: build-before-quiesce, release install/activate, three-observation frontend readiness, post-commit pruning

- [ ] **Step 1: Add deployment ordering and mixed-generation tests**

```python
def test_panel_build_precedes_service_quiescence(deploy_text):
    assert deploy_text.index("hy2-build-panel.sh") < deploy_text.index("stop_units_for_deploy")

def test_readiness_rejects_entry_referencing_missing_asset(fake_panel):
    fake_panel.remove_referenced_asset()
    assert fake_panel.readiness() is False
```

- [ ] **Step 2: Run and observe missing integration**

Run: `pytest tests/test_panel_deploy_integration.py -v`

Expected: failures because deploy does not build/install panel releases.

- [ ] **Step 3: Integrate prebuild, install, and transactional activation**

Before prepare/quiescence, build into a temporary directory and run `hy2-panel-release.py install`. During the durable transaction, snapshot the old managed pointer and activate the new ID. On any later failure, outer recovery restores the old validated target.

- [ ] **Step 4: Extend readiness**

Health checks fetch Python `/healthz`, SPA `/login`, parse its same-origin hashed JS/CSS references, fetch each representative asset, verify the active release ID response header, check `no-store`/immutable caching and CSP, then require three consecutive observations. No token-bearing URL is used.

- [ ] **Step 5: Commit, prune, and retain rollback release**

Only after durable commit remove staging directories and prune releases while preserving current and previous. A first deployment with no previous link is valid and recoverable to an absent frontend pointer plus existing nginx config snapshot.

- [ ] **Step 6: Run deployment and readiness suites**

Run: `pytest tests/test_panel_deploy_integration.py tests/test_deploy_durable_recovery.py tests/test_operational_health_readiness.py tests/test_https_activation_transaction.py -v`

Expected: all pass.

- [ ] **Step 7: Commit deployment cutover machinery**

```bash
git add deploy.sh scripts/hy2-health-check.sh tests/test_panel_deploy_integration.py tests/test_deploy_durable_recovery.py tests/test_operational_health_readiness.py
git commit -m "feat(deploy): activate React panel atomically"
```

### Task 6: Remove Python HTML rendering and legacy frontend assets

**Files:**
- Modify: `hysteria/subscription_service.py`
- Modify: `hysteria/usage_dashboard.py`
- Modify: `hysteria/codex_dashboard.py`
- Modify: `hysteria/incident_console.py`
- Modify: `hysteria/health_widgets.py`
- Delete: `hysteria/admin.css`
- Delete: `hysteria/admin_poll.js`
- Delete: `hysteria/usage.js`
- Delete: `hysteria/codex_quota.js`
- Delete: `hysteria/static/home.js`
- Move: `hysteria/static/fonts/inter-var.woff2` to `frontend/public/fonts/inter-var.woff2`
- Move: `hysteria/static/fonts/jetbrains-mono.woff2` to `frontend/public/fonts/jetbrains-mono.woff2`
- Create: `tests/test_panel_cutover.py`

**Interfaces:**
- Produces: API/backend-only Python service and React-only frontend

- [ ] **Step 1: Add a failing no-legacy static/source test**

```python
def test_python_panel_has_no_html_renderer_or_legacy_assets():
    source = Path("hysteria/subscription_service.py").read_text()
    for forbidden in ("def html_page(", "def render_admin_shell(", "def render_login(", "admin_poll.js", "usage.js"):
        assert forbidden not in source
    for path in LEGACY_ASSETS:
        assert not Path(path).exists()
```

- [ ] **Step 2: Run and confirm it fails on current legacy code**

Run: `pytest tests/test_panel_cutover.py -v`

Expected: failures list all legacy renderers/assets.

- [ ] **Step 3: Remove document rendering and legacy static routes**

Delete `html_page`, navigation/icon/alert shell helpers, all public/user/admin `render_*` page functions, inline scripts, static byte loading/ETags, and `/static/style.css`/legacy JS branches. Keep backend-owned YAML/SVG/download/token routes, bounded HTTP infrastructure, API mount, cookies, and business/service calls.

- [ ] **Step 4: Remove render-only helpers from supporting modules**

Delete HTML/SVG page composition in `usage_dashboard`, `codex_dashboard`, `incident_console`, and `health_widgets` after confirming API model builders have no renderer imports. Move fonts to Vite public assets and reference them from bundled CSS.

- [ ] **Step 5: Delete legacy source-assertion tests and preserve behavior tests**

Delete `tests/test_static_js.py`, `tests/test_admin_poll_regressions.py`, `tests/test_frontend_p1_regressions.py`, `tests/test_mobile_css_accessibility_regressions.py`, and `tests/test_semantic_heading_regressions.py`. Remove only HTML/static assertions from `tests/test_product_ux_regressions.py`, `tests/test_usage_page.py`, `tests/test_codex_quota.py`, and `tests/test_smoke.py`; retain backend schema, policy, security, data, and route tests. Their React equivalents must already pass in Vitest/Playwright before deletion.

- [ ] **Step 6: Run no-legacy, full Python, and frontend suites**

Run: `pytest tests/test_panel_cutover.py -v && pytest -q`

Run: `cd frontend && npm run typecheck && npm run lint && npm run test && npm run build && npm run check:bundle && npm run e2e`

Expected: all pass and `rg 'render_admin_shell|admin_poll\.js|/static/style\.css' hysteria tests frontend` returns no legacy implementation reference.

- [ ] **Step 7: Commit legacy removal**

```bash
git add -A hysteria frontend tests
git commit -m "refactor(panel): remove legacy server-rendered frontend"
```

### Task 7: Documentation, complete verification, and completion audit

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CONTEXT.md`
- Create: `docs/react-panel-operations.md`
- Modify: `scripts/hy2-preflight.sh`

**Interfaces:**
- Produces: accurate development/deployment/rollback operations docs and final proof report inputs

- [ ] **Step 1: Update architecture and developer documentation**

Document exact Node version/checksums, `npm ci --ignore-scripts`, development commands, API v1, route ownership, Vite release layout, CSP, build budgets, E2E commands, atomic rollback, WebGL fallback, and troubleshooting. Remove every statement that says Python renders the panel HTML or serves legacy assets.

- [ ] **Step 2: Run documentation consistency scans**

Run: `rg -n 'server-rendered|admin_poll\.js|usage\.js|static/style\.css|Python.*HTML' README.md README.zh-CN.md CONTEXT.md docs`

Expected: matches occur only in historical design/plan documents that explicitly describe the legacy state.

- [ ] **Step 3: Run complete automated verification**

Run: `./scripts/hy2-preflight.sh`

Run: `cd frontend && npm run e2e`

Run: `pytest tests/test_deploy_durable_recovery.py tests/test_panel_deploy_integration.py tests/test_panel_nginx.py -v`

Expected: every command passes.

- [ ] **Step 4: Inspect production artifacts and route ownership**

Run: `python3 scripts/hy2-panel-release.py validate frontend/dist`

Run: `find frontend/dist -type l -o -name '*.map'`

Expected: validator succeeds and `find` prints nothing.

Run: `rg -n "unsafe-inline" nginx hysteria/subscription_service.py`

Expected: no `script-src` policy contains `unsafe-inline`; any style-policy match is documented and nonce/hash constrained.

- [ ] **Step 5: Audit every spec completion criterion**

Record evidence in the final task summary for all 11 completion criteria: React routes, API ownership, no Python HTML, deleted legacy assets, preserved security/concurrency/recovery, visual fallbacks, all test families, caching/CSP, deployment fault injection, docs, and untouched operator changes.

- [ ] **Step 6: Commit final documentation**

```bash
git add README.md README.zh-CN.md CONTEXT.md docs/react-panel-operations.md scripts/hy2-preflight.sh
git commit -m "docs: document React panel operations"
```

## Phase Acceptance

- Production UI routes are React-owned and backend routes remain exact.
- Vite entry/assets activate and roll back as one validated generation.
- Python generates no panel HTML and serves no legacy frontend asset.
- Strict CSP, cache rules, query-redacted logging, and release containment pass.
- Full Python, frontend, browser, accessibility, build-budget, nginx, deployment, and recovery suites pass.
- Documentation matches the deployed architecture.
- The operator's pre-existing uncommitted Clash changes remain untouched.
