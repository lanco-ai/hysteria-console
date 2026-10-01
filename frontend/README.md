# React frontend

This directory contains the production React/Vite frontend for the public home,
authentication, administrator console, and authenticated user panel. The
document routes are served by the FastAPI adapter on port 8083 and use the
immutable assets under `/static/react/assets/`.

## Public shopping and AI entry

The home page uses a shared public header with 购物, AI 对话 and AI 视频.
Shopping is the default view; `/?view=chat` and `/?view=video` select the existing
AI interfaces. `/login` and `/auth` open the existing administrator login modal
over this entry; `/user/login` retains the existing user login realm.
The authenticated header has a management-console link, and the console logo
returns to this public entry. AI links no longer appear in the admin sidebar.

The product source is still pending. The shopping directory honestly displays
暂无商品; no example products, prices, inventory or sales counts are published.
The category/search layout is ready for the later catalogue integration; there
is no new checkout, payment, registration or order-query subsystem.

The original chat and video components run inside the public page shell after
administrator login, using unchanged APIs and stores. Guests can see the chat
layout and video introduction but cannot access histories, projects, workflows,
generation APIs or provider settings. The exact `/admin/chat` and `/admin/video`
documents remain administrator guarded for compatibility, including bookmarked
conversation IDs; unsafe login query parameters are still discarded.
Unknown paths retain their 404 behavior. The HTTPS template serves `/` from the
same FastAPI document service rather than redirecting it away from this entry.
When publishing this change, install the updated document router together with
the built frontend, and verify that the active HTTPS root route proxies to
port 8083. This keeps initial document metadata and bookmarked login returns
aligned with the new public entry.

## Document routes

The explicit React document allow-list covers:

- `/`, `/login`, `/user/login`, `/logout`, `/user/logout`
- `/user/panel`, `/user/change-password`
- `/admin`, `/admin/logs`, `/admin/settings`, `/admin/usage`, `/admin/health`
- `/admin/incidents`, `/admin/config`, `/admin/rules`,
  `/admin/landing-egresses`, `/admin/github-trending`, and `/admin/user/<uid>`

The document shell injects the request host and the appropriate session guard;
unknown paths are not treated as SPA fallbacks. Page data and mutations use the
cookie-authenticated `/api/v1/*` adapters.

The administrator's 开源发现 page at `/admin/github-trending` reads the daily
and weekly GitHub Trending snapshots through `/api/v1/github-trending`. Search
and language filtering apply only to the currently displayed snapshot. Manual
refresh requests use the same-origin `/api/v1/github-trending/refresh` endpoint;
its cooldown and upstream retry fields are authoritative.

## Daily workspace

`/admin/plans` keeps the shared date header and plan quadrants on the main page.
The 时间线 and 回顾 links open a native modal side panel (full screen on phones),
with direct writing in the timeline content box. The separate record form,
record-type/time fields, optional-field controls, and save button have been removed.
Typing pauses for 1.2 seconds or leaving the box saves automatically; Ctrl/Cmd+Enter
also saves immediately. Continued typing updates the same record. The small plus
control after a successful save starts another entry. New entries use the selected
date and category (all categories defaults to life); the review box creates a
weekly recap in the selected week. Older structured fields and chat provenance
remain intact when editing a record's body. Search and category filtering still
read all dates, as the toolbar note states.

Chat's “保存到记录” action saves to the same timeline with its source link.
Existing `#daily-timeline` and `#daily-review` links open the relevant panel;
`#daily-plans` closes it. Escape and close return focus to the main page.
Unsaved text remains in memory while closed; failed saves stop automatic retries
and retain the text for explicit retry. Leaving/reloading before a save finishes
uses the existing unsaved-change guard. After a failed save, “重新读取” requires
confirmation before discarding unsaved text and loading the server's records.
Saved text persists on the server;
unsaved text is not stored in browser storage.

Autosave serializes requests and uses an optional UUID-v4 `Idempotency-Key` on
journal creation. The server deduplicates keyed creates under the existing file
lock without a separate cache; existing clients without a key retain their API
behavior. Updates keep the existing revision check. A lost update response is
reconciled only when the server record exactly matches the submitted fields.
No journal text is sent to an AI service.

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
