<h1 align="center">hy2</h1>

<p align="center">
  <b>One-shot Hysteria2 + Xray (VLESS&nbsp;Reality) deploy with a built-in subscription &amp; admin panel.</b>
</p>

<p align="center">
  <a href="#features"><img alt="status" src="https://img.shields.io/badge/status-ready-4ade80?style=flat-square"></a>
  <a href="#one-shot-deploy"><img alt="platform" src="https://img.shields.io/badge/platform-Debian%20%7C%20Ubuntu-60a5fa?style=flat-square"></a>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-d1d5db?style=flat-square">
  <a href="README.zh-CN.md"><img alt="lang" src="https://img.shields.io/badge/lang-中文-f87171?style=flat-square"></a>
</p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#one-shot-deploy">Deploy</a> ·
  <a href="#admin-panel">Panel</a> ·
  <a href="#configuration">Config</a> ·
  <a href="#security-notes">Security</a>
</p>

---

## Features

- ⚡  **Hysteria2** on `:443/udp` with Salamander obfs + UDP port-hopping over `20000-40000/udp`.
- 🛡️ **Xray VLESS&nbsp;+&nbsp;Reality** on `:443/tcp` (primary) and `:8443/tcp` (backup), masquerading as `www.bing.com`.
- 🎛️ **Built-in admin panel** — create users, see live traffic, manage subscription template & route rules from a browser. Sidebar layout, dark theme, fully responsive.
- 📊 **Per-user quota & device limit** — enforced by a 15-second job that pulls hysteria + xray stats, kicks over-quota users, and resets on a configurable billing cycle (default: day-12 anchor, 30-day cycle).
- 🔗 **Per-user subscription URL** that emits Clash YAML on the fly with the right password & UUID injected.
- 🚀 **One-shot deploy** — fill in `.env`, run `./deploy.sh`, get a working stack in under a minute.

## Architecture

```
        ┌──────────────────────────────────────────────┐
        │  Client (Clash / sing-box / mihomo)          │
        └───────────────────┬──────────────────────────┘
                            │ subscribe → http://host/sub/<user>?token=…
                            ▼
        ┌──────────────────────────────────────────────┐
        │  nginx :80   →   subscription_service.py     │
        │                  127.0.0.1:8081              │
        └───────┬────────────────┬─────────────────────┘
                │ /admin         │ /sub/<user>
                ▼                ▼ (renders template.yaml)
        ┌──────────────┐    ┌──────────────────────────┐
        │   panel UI   │    │  users.json + usage.json │
        └──────────────┘    └──────────────────────────┘

                  data plane
        ┌──────────────────────────────────────────────┐
        │  hysteria2 :443/udp  +  port-hop 20000-40000 │
        │  xray vless+reality  :443/tcp & :8443/tcp    │
        └──────────────────────────────────────────────┘
```

## One-shot deploy

```bash
git clone https://github.com/lhzyyds666/hy2.git
cd hy2
cp .env.example .env
$EDITOR .env             # fill in every value (each line tells you how)
sudo ./deploy.sh
```

`deploy.sh` will:

1. Install the official `hysteria` and `xray` binaries.
2. Render every config template with your `.env` values.
3. Drop files into `/root/hysteria/`, `/usr/local/etc/xray/`, `/etc/systemd/system/`.
4. Generate a self-signed TLS cert for Hysteria if missing.
5. `systemctl daemon-reload`, enable & start every unit, and install the nginx reverse proxy.

### Required `.env` keys

| Key | How to generate |
|---|---|
| `HY_SERVER_HOST` | your VPS public IP or domain |
| `HY_API_SECRET` | `openssl rand -hex 24` |
| `HY_OBFS_PASSWORD` | `openssl rand -base64 24 \| tr -d '/+='` |
| `XRAY_REALITY_PRIVATE_KEY` / `_PUBLIC_KEY` | `xray x25519` |
| `XRAY_REALITY_SHORT_ID` | `openssl rand -hex 8` |
| `XRAY_CLIENT_UUID` | `xray uuid` (or `uuidgen`) |

### Optional `.env` keys

| Key | Default | Notes |
|---|---:|---|
| `HY_DISPLAY_MULTIPLIER` | `2.28` | Display/billing multiplier applied to raw Hysteria/Xray/TUIC traffic counters. Keep it aligned with your provider accounting. `/admin/health` shows a calibration suggestion after enough traffic samples. Valid range: `0.1`-`20.0`. |
| `HY_ENABLE_HTTPS` | `0` | Set to `1` only when `HY_SERVER_HOST` is a real DNS name pointed at this VPS. |
| `HY_CERTBOT_EMAIL` | empty | Required when `HY_ENABLE_HTTPS=1`. |

## Admin panel

After deploy:

- **Admin** — `http://<server>/admin` — log in at `/login`. The admin password is **not** set via a first-visit form. On first deploy, write `admin_pass` (plaintext) into `subscription_meta.json`; on the next start it is migrated to a salted PBKDF2 `admin_pass_hash` and the plaintext is removed. If no password is configured, a random one is generated and written to a root-only file `admin_initial_password.txt` (next to `subscription_meta.json`, mode 0600) so you can read it, log in, and rotate it — then delete the file. Rotate the password any time from `/admin/settings` — that also signs out all other sessions while keeping you logged in on the current device.
- **Add a user** from the panel → instant subscription URL `http://<host>/sub/<name>?token=<token>`.
- **Per-user actions** — each row can edit the plan, set an expiry date, add extra quota, keep an operator note, reset/refresh usage, **rotate the subscription token** (instantly invalidate a leaked link), **suspend/resume** (disable without deleting: reject new connections, pull the xray inbound, and drop live sessions), and delete. Expired users are rejected by auth and removed from static Xray/TUIC plans until renewed.
- **User panel** — `http://<server>/panel/<user>?token=<token>` — per-user usage + device stats, with a quota-reset countdown, a 30-day usage trend, one-click copy for the subscription/panel links, multi-profile subscription exports, and live usage refresh every 10s (paused while the tab is hidden).
- **Subscription profiles** — `profile=game|work|lowdata|safe` can be appended to `/sub/<user>?token=...`. `game` favors UDP/low latency, `work` favors TCP fallback stability, `lowdata` routes unknown traffic direct, and `safe` proxies everything except LAN/private traffic. Omitting `profile` keeps the shared template unchanged.
- **Template config** — edit the shared Clash YAML template inline (JSON view, validation, format/collapse).
- **Route rules** — add / remove / re-order proxy/direct/reject rules; live diff against the template.
- **Reset log** — full audit trail of every traffic-reset action.
- **Settings** — `http://<server>/admin/settings` — change the admin password in-browser (verifies the current password, then stores a fresh PBKDF2 hash). Changing it signs out every existing session and re-issues a session cookie for the current device.
- **Health** — alongside the 6 status cards, a "send test alert" button verifies the Telegram / webhook channels in `alerts.json`. The page also includes a line radar that compares recent Hysteria/Xray/TUIC protocol traffic and recommends a subscription profile, plus a cost calibrator that compares public NIC counters with app-level raw traffic and suggests a `HY_DISPLAY_MULTIPLIER`. TUIC traffic is port-level aggregate metering, not per-user quota metering. Alert tests are dispatched on a background thread (non-blocking); the webhook URL is operator-supplied (admin-equivalent trust) with no URL allowlisting, so confirm delivery at the receiver.

The panel polls `/admin/usage.json` every 5s, automatically pauses when the tab is hidden, and uses an in-memory row index so it doesn't re-query the DOM on each tick.

## Configuration

### Port layout on the server

| Port | Service |
|---|---|
| `80/tcp` | nginx → reverse-proxies `127.0.0.1:8081` (panel & subscriptions) |
| `443/tcp` | Xray — VLESS + Reality |
| `443/udp` | Hysteria2 |
| `8443/tcp` | Xray — VLESS + Reality (backup) |
| `9443/udp` | TUIC v5 |
| `20000-40000/udp` | iptables REDIRECT → `443/udp` (port-hopping) |

### Backup

After deploy, run:

```bash
sudo /usr/local/sbin/hy2-backup.sh
```

Backups are written to `/root/hysteria/backups/` by default, mode 0600, with a `.sha256` checksum file. The archive includes users, admin metadata, subscription template, alert config, runtime state (excluding live admin sessions), TLS/API secrets, TUIC config, and Xray config.

Optional encryption:

```bash
sudo install -m 600 /dev/null /root/hysteria/backup.pass
sudo sh -c 'openssl rand -base64 32 > /root/hysteria/backup.pass'
sudo HY2_BACKUP_PASSPHRASE_FILE=/root/hysteria/backup.pass /usr/local/sbin/hy2-backup.sh
```

Before restoring anything, dry-run the archive:

```bash
sudo /usr/local/sbin/hy2-restore-check.sh /root/hysteria/backups/hy2-backup-YYYYMMDDTHHMMSSZ.tar.gz
sudo HY2_RESTORE_PASSPHRASE_FILE=/root/hysteria/backup.pass /usr/local/sbin/hy2-restore-check.sh /root/hysteria/backups/hy2-backup-YYYYMMDDTHHMMSSZ.tar.gz.enc
```

The dry-run decrypts if needed, validates archive paths, checks JSON/YAML parseability, and reports how many live runtime files would be overwritten. It never writes back into `/root/hysteria`.

### HTTPS for the admin panel

For a real domain already pointed at the VPS:

```bash
sudo /usr/local/sbin/hy2-enable-https.sh panel.example.com you@example.com
```

Or set `HY_ENABLE_HTTPS=1` and `HY_CERTBOT_EMAIL=you@example.com` in `.env` before running `deploy.sh`. The helper installs certbot, requests an nginx certificate, enables HTTP-to-HTTPS redirect, and reloads nginx.

### Files NOT in git (by design)

These are per-server secrets or runtime state — never commit them. They are already in `.gitignore`:

- `.env` — real secret values
- `server.crt`, `server.key` — TLS cert (auto-generated by `deploy.sh`)
- `users.json` — user roster with password hashes & `sub_token`s
- `subscription_meta.json` — admin password hash
- `state/` — usage counters, online snapshot, reset log

## Security notes

- The subscription service binds **only** to `127.0.0.1:8081`. The public surface is nginx on `:80`.
- All template files use `__PLACEHOLDER__` markers; the **real secrets only ever live in `.env` and the rendered files under `/root/hysteria/`**, both of which are gitignored.
- Hysteria management API is localhost-only and gated by `HY_API_SECRET`. Treat that secret like an SSH key.
- Admin auth uses PBKDF2-SHA256 with 200k rounds + per-secret salt; sessions use HTTP-only `SameSite=Lax` cookies.
- Per-user `sub_token` is a 24-byte URL-safe random; rotating it instantly invalidates a leaked subscription URL without affecting the user record.
- Set up a real TLS cert for the admin/subscription panel before exposing it on the public internet. Use `/usr/local/sbin/hy2-enable-https.sh <domain> <email>`; the bundled cert is self-signed for the Hysteria endpoint only.

> **Rotation log** — an early commit (pre-`e7d9d3a`) accidentally embedded a real `HY_API_SECRET` in source. It was rotated and verified invalid on 2026-04-30; the value present in pre-`e7d9d3a` git history no longer authenticates against any live endpoint.

## Project layout

```
.
├── deploy.sh                       # one-shot installer
├── .env.example                    # secrets template (copy → .env)
├── hysteria/
│   ├── config.yaml.tpl             # hysteria2 server config
│   ├── auth_backend.py             # auth-by-command bridge
│   ├── subscription_service.py     # admin panel + /sub renderer
│   ├── traffic_limiter.py          # 5-sec job: stats + auto-kick
│   └── clash-default.yaml.tpl      # subscription template
├── xray/config.json.tpl            # vless+reality config
├── nginx/hysteria-panel.conf       # :80 reverse proxy
├── scripts/hysteria-porthop.sh     # iptables port-hop script
└── systemd/                        # unit files for every service
```

## Alerts (optional)

Drop a `/root/hysteria/alerts.json` (chmod 600) to enable Telegram / webhook alerts:

```json
{
  "telegram": {"bot_token": "...", "chat_id": "..."},
  "webhook":  {"url": "https://example.com/hook", "secret": "optional-hmac-key"},
  "anomaly_z_threshold": 3.0,
  "anomaly_min_bytes": 1073741824
}
```

- 80% / 100% quota crossings → one push per user per billing month
- Daily total exceeding `z_threshold` σ above the trailing 7-day mean → one push per user per day
- Expiry reminders → one push when a user is within `expiry_warn_days` (default 3) of `expires_at`, plus one push after expiry
- Webhook payloads are HMAC-SHA256 signed via `X-Hy2-Signature: sha256=<hex>` when `secret` is set

If the file is absent, the dispatcher is a no-op. Live infra heartbeat at `/admin/health` (6 cards: cron pulse / hysteria / xray / disk / TLS cert / online users, auto-refreshes every 30s).

## Development

For local test runs:

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

## License

MIT — see `LICENSE` if present, otherwise treat as MIT.

---

<p align="center"><sub>中文文档 → <a href="README.zh-CN.md">README.zh-CN.md</a></sub></p>
