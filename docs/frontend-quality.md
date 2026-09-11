# Frontend maintenance

The console remains Python-rendered HTML with vanilla JavaScript. Node is a
development/CI dependency, not a production runtime dependency.

## Local checks

Use Node 22 (at least 22.13) and Python 3.12, matching CI:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci --ignore-scripts
npx playwright install --with-deps chromium
PATH="$PWD/.venv/bin:$PATH" npm run check:frontend
PYTHON=.venv/bin/python bash scripts/check-quality.sh
```

`check:frontend` checks CSS assembly without writing files, lints JavaScript and
CSS, runs gate/helper tests, then runs browser tests. The runner owns an ephemeral
loopback server and temporary fictional data. It rejects production file access,
external service connections and service commands; it does not need live accounts.

Browser coverage includes login controls, admin five-column management, responsive
layouts, configuration formatting, cancelled rule deletion, hourly/history tables,
subscription copy/QR, and user polling visibility, retry and disabled-account states.
It is not an exhaustive end-to-end test of every administrative operation.

## Ownership

- Edit styles in `hysteria/styles/`; `manifest.json` specifies cascade order.
  Run `npm run build:css` and commit both sources and generated `hysteria/admin.css`.
  The public URL remains `/static/style.css`.
- Keep late workspace overrides in their documented order until browser evidence
  supports consolidation. Do not delete selectors solely because text search finds
  no references; some classes are created dynamically.
- Login, user, configuration and shell behavior lives in named scripts under
  `hysteria/static/`. Templates pass escaped attributes, not executable string data.
- `ui-core.js` owns shared formatting, escaping and timeout requests. Pages retain
  their own polling, cancellation and visibility lifecycle. Shared helpers load
  before consumers; shell confirmation handlers register before admin handlers.
- Fixed layout belongs in component classes; dynamic progress widths may remain
  inline. Reuse semantic color variables and preserve public/workspace distinctions.

## Adding an asset

Register new named scripts in `hysteria/web_assets.py`; its explicit allowlist and
content digest provide serving and cache versioning. Update deployment installation,
durable artifact and snapshot lists in `deploy.sh`, plus the recovery allowlist in
`scripts/hy2-deploy-recovery.py`. Add serving/cache tests and browser interaction
coverage. Do not bypass this registry with arbitrary filesystem paths.

CI runs backend and frontend quality jobs independently. Before release, review the
diff, run both checks, and separately verify the deployment target and rollback path.
Local checks do not prove that GitHub CI or production deployment has run.
