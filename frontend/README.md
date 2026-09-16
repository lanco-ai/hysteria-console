# React frontend

This directory contains the production React/Vite frontend for the public home,
authentication, administrator console, and authenticated user panel. The
document routes are served by the FastAPI adapter on port 8083 and use the
immutable assets under `/static/react/assets/`.

## Document routes

The explicit React document allow-list covers:

- `/`, `/login`, `/user/login`, `/logout`, `/user/logout`
- `/user/panel`, `/user/change-password`
- `/admin`, `/admin/logs`, `/admin/settings`, `/admin/usage`, `/admin/health`
- `/admin/incidents`, `/admin/config`, `/admin/rules`,
  `/admin/landing-egresses`, and `/admin/user/<uid>`

The document shell injects the request host and the appropriate session guard;
unknown paths are not treated as SPA fallbacks. Page data and mutations use the
cookie-authenticated `/api/v1/*` adapters.

## Asset ownership

`frontend/src/styles/index.css` imports the ordered sections listed in
`frontend/src/styles/manifest.json`. Vite emits hashed CSS, JavaScript, and
font assets in `frontend/dist/assets`. React documents do not load
`/static/style.css` or any legacy page script. The old CSS build chain and
page-specific JavaScript were removed; `/static/style.css` and retired script
URLs return 404 from the compatibility listener.

## Compatibility boundary

The following interfaces remain on the legacy subscription service (8081) and
must not be removed during the React migration:

- `/sub/<user>` subscription downloads and profile variants
- `/panel/<user>` token exchange, `/panel/<user>.json`, and QR endpoints
- `/admin/usage.csv`, `/admin/incidents/evidence.json`, and registered legacy
  JSON/download routes

Nginx keeps these locations separate from `/static/react/assets/` and the
`/api/v1/*` FastAPI routes. Direct browser document paths on internal 8081 are
explicitly rejected so the Python service cannot emit a second HTML frontend.
The existing certificates, domain, 443/9444 listeners, proxy configuration,
and backend domain services are unchanged.

## Checks

Use a Python virtual environment containing the repository test dependencies.
For the maintained quality environment in this checkout:

```sh
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run typecheck:react
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run build:react
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run test:react-browser
```

Before a release, run `npm run check:frontend` and the backend route suites.
Validate the immutable release with:

```sh
python3 scripts/hy2_panel_release.py validate frontend/dist
```

The deployment flow stages the complete `frontend/dist` tree, atomically
updates `/root/hysteria/panel/current`, and keeps the previous release for
rollback. A green local build is not, by itself, evidence that production has
been deployed; production smoke checks must verify the document and
compatibility boundaries above.
