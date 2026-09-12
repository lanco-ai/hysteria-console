# Shared module deployment contract repair

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Keep the legacy release deployable after the shared log-reader extraction, without activating React/FastAPI or running deployment.

**Architecture:** Register the newly required pure Python module in the existing exact copy/snapshot/recovery/permission inventories. Add a source-only dependency-closure check so future module extraction cannot silently omit a required runtime file.

**Tech Stack:** Existing shell deployment, Python AST/pytest and durable recovery helper; no new dependencies.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444, runtime secrets, proxy configuration and all persistent user data.
- Frontend/backend compatibility and runtime deployment are tested as a pair; rollback switches the matched pair, not an arbitrary subset of assets.
- This task edits source inventories and offline tests only. Never run deploy.sh, invoke real services, or read/copy live files under /root/hysteria.
- Keep exact artifact allowlists and old-backup compatibility entries. Do not add wildcard recovery paths, state data, sessions, secrets or new framework runtime files to compensate for this one missing helper.

## Confirmed cause

`operations_views.py` now imports `reset_log_data.py` at module load. The
66 Python `render` source entries in deploy.sh omit it. A read-only AST closure
check over those declared modules found exactly:

```text
Missing local imports: [('operations_views.py', 'reset_log_data.py')]
```

Existing deployment/recovery inventory-equality tests compare two equally
incomplete lists, so they passed. The new source import is therefore invisible
to those tests. The current deployed service has not changed; the gap would
affect a future source release unless repaired before deployment.

### Task 1: Register and regression-test the extracted runtime helper

**Files:** Modify `deploy.sh`, `scripts/hy2-deploy-recovery.py` and `tests/test_deploy_durable_recovery.py`. No production code or configuration changes are needed.

**Interfaces:** `build_durable_artifact_set`, the existing snapshot artifact loop,
the Python `render` calls, the source chmod 700 list, and recovery helper
`EXACT_ALLOWED_PATHS` must all contain reset_log_data.py, beside operations_views.py.
The destination is exactly `/root/hysteria/reset_log_data.py` (expressed as
`$HY_DIR/reset_log_data.py` in deploy.sh). Keep established snapshot-before-copy
and recovery protocols unchanged.

- [ ] Add a failing source-only pytest that collects literal Python `render "$REPO_DIR/hysteria/...py"` source basenames, parses their Python AST without importing/executing modules, and follows local flat .py imports. Assert every reachable local file is installed. Walk Import and absolute ImportFrom; ignore standard-library/third-party imports with no corresponding source file. Use a visited set. Report `(importer, missing_file)` pairs so failures are actionable. Do not load source credentials or execute deployment just to inspect imports.

```python
tree = ast.parse((ROOT / 'hysteria' / filename).read_text(encoding='utf-8'))
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        modules = [alias.name.split('.')[0] for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        modules = [node.module.split('.')[0]]
    else:
        modules = []
    for module in modules:
        dependency = module + '.py'
        if (ROOT / 'hysteria' / dependency).is_file():
            if dependency not in installed:
                missing.add((filename, dependency))
            pending.append(dependency)
assert not missing, sorted(missing)
```

- [ ] Add a focused contract test for the helper's exact durable artifact,
snapshot-before-copy presence, rendered destination, chmod list and recovery
allowlist. Reuse `_shell_function` and `_python_literal` already in the test
module and the existing whole allowlist-equality test. Run the new tests red;
expect the missing operations_views → reset_log_data dependency and missing
registration, not an unrelated fixture failure.

- [ ] Add the one helper at all five existing registration points. The render
line is exactly equivalent to:

```bash
render "$REPO_DIR/hysteria/reset_log_data.py" "$HY_DIR/reset_log_data.py"
```

The helper recovery allowlist adds exactly `/root/hysteria/reset_log_data.py`.
Do not register or install web_api, frontend/dist, ASGI servers or dependencies;
framework cutover remains a separate explicitly approved future task.

- [ ] Run the new tests and `tests/test_deploy_durable_recovery.py`,
`tests/test_deploy_security_regressions.py`, `tests/test_reset_log_data.py` and
`tests/test_panel_view_modules.py` with the existing venv. Run backend lint-only,
`bash -n deploy.sh`, and `git diff --check`. The recovery tests use their existing
temporary test root; never point them at live state.

- [ ] Review and commit only these source/test changes locally. Record red/green
commands, installed dependency-closure count and test outputs. No push or deploy.

## Scope check

This repairs release packaging for an already shared legacy dependency. It does
not claim framework deployment readiness or replace the separate staging,
immutable-asset, HTTP compatibility, cutover approval or rollback gates.
