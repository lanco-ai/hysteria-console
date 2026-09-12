# React frontend preview

This directory contains the built React preview used during the incremental frontend migration. It does not cut production traffic over to React.

The preview has three controlled entries:

- `/__react/` renders the public home using illustrative static data and makes no API requests.
- `/__react/login` renders the administrator login and submits to the real JSON login adapter using fictional, temporary preview credentials.
- `/__react/admin/logs` renders the administrator reset log and reads the real cookie-authenticated `/api/v1/session` and `/api/v1/admin/logs` endpoints.

The existing public routes, including `/` and `/admin/logs`, remain unchanged for side-by-side comparison. Both React entries reuse `/static/style.css` and its local fonts; they do not load the legacy home, shell, or UI scripts.

## Checks

Use a Python virtual environment containing the repository's test dependencies. For the maintained quality environment in this checkout:

```sh
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run typecheck:react
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run build:react
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run test:react-browser
```

`npm run test:react-browser` serves the real Vite production artifact through a guarded, loopback-only preview with fictional backend state. Only POST `/api/v1/login` may mutate temporary preview sessions. Every other POST, including `/login` and `/logout`, remains blocked with 405; no production state or logout mutation is performed.

These commands validate preview-only migration work. They do not publish assets, change nginx routing, or deploy to the runtime host.
