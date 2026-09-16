# Frontend maintenance (React-only frontend)

The browser frontend is the Vite-built React application. Python serves the
document shell and API responses; it is no longer the source of browser HTML.
Node remains a development/CI dependency, not a production runtime dependency.

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

`check:frontend` lints the React JavaScript and CSS, runs gate/helper tests, then
runs browser tests. The runner owns an ephemeral
loopback server and temporary fictional data. It rejects production file access,
external service connections and service commands; it does not need live accounts.

Browser coverage includes login controls, admin five-column management, responsive
layouts, configuration formatting, cancelled rule deletion, hourly/history tables,
subscription copy/QR, and user polling visibility, retry and disabled-account states.
It is not an exhaustive end-to-end test of every administrative operation.

## Ownership

- Edit styles under `frontend/src/styles/`. The local `manifest.json` records
  cascade order and `index.css` imports those sections. Vite emits hashed CSS in
  `frontend/dist/assets`; do not restore the retired `/static/style.css` chain.
- Keep late workspace overrides in their documented order until browser evidence
  supports consolidation. Do not delete selectors solely because text search finds
  no references; some classes are created dynamically.
- Login, user, configuration and shell behavior lives in React components and
  hooks under `frontend/src/`; templates pass escaped bootstrap data, not
  executable string data.
- Fixed layout belongs in component classes; dynamic progress widths may remain
  inline. Reuse semantic color variables and preserve public/workspace distinctions.

## Adding an asset

Add browser assets to `frontend/src/` and let Vite fingerprint them. Update the
React release validation and deployment allowlist when adding a new generated
asset. API-only downloads remain explicit Python routes; do not add browser HTML
or static page scripts to the compatibility listener.

CI runs backend and frontend quality jobs independently. Before release, review the
diff, run both checks, and separately verify the deployment target and rollback path.
Local checks do not prove that GitHub CI or production deployment has run.
