"""User-panel billing countdown and live-refresh payload composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Context:
    local_now: Callable[..., object]
    get_cycle_length_days: Callable[..., int]
    cycle_start_for: Callable[..., object]
    load_json: Callable[..., object]
    scaled_usage_for_user: Callable[..., object]
    user_total_quota: Callable[..., int]
    configured_max_devices: Callable[..., int]
    pct: Callable[..., float]
    USAGE_DAILY_FILE: Path
    ONLINE_FILE: Path


def _cycle_reset_info(ctx: Context, now=None):
    """Return (next_reset_date_str, days_left, cycle_length_days) for the panel
    quota-reset countdown. days_left is at least 1 — today is always strictly
    before the next cycle boundary."""
    if now is None:
        now = ctx.local_now()
    cycle_len = ctx.get_cycle_length_days()
    next_reset = (ctx.cycle_start_for(now) + timedelta(days=cycle_len)).date()
    days_left = max((next_reset - now.date()).days, 0)
    return next_reset.strftime('%Y-%m-%d'), days_left, cycle_len


def _build_panel_json_payload(ctx: Context, user, cfg, *, now=None):
    """Live-refresh payload for the end-user panel (/panel/<user>.json).
    Mirrors the at-load values render_user_panel computes, in displayed bytes."""
    if now is None:
        now = ctx.local_now()
    daily = ctx.load_json(ctx.USAGE_DAILY_FILE, {})
    tx, rx, used = ctx.scaled_usage_for_user(user, daily=daily, now=now)
    total = ctx.user_total_quota(cfg)
    remain = max(total - used, 0) if total > 0 else -1
    online = int(ctx.load_json(ctx.ONLINE_FILE, {}).get(user, 0) or 0)
    return {
        'ts': now.isoformat(timespec='seconds'),
        'used_bytes': int(used),
        'total_bytes': int(total),
        'remain_bytes': int(remain),
        'tx_bytes': int(tx),
        'rx_bytes': int(rx),
        'online': online,
        'max_devices': ctx.configured_max_devices(cfg),
        'percent': round(ctx.pct(used, total), 2),
    }
