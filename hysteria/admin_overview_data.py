"""Data-only presentation builders for the administrator overview."""

from datetime import timedelta

import user_compat


def build_user(ctx, user, cfg, online, base_url, *, daily=None, now=None):
    """Build one allowlisted overview row without presentation markup."""
    effective_now = now or ctx.local_now()
    tx, rx, used = ctx.scaled_usage_for_user(user, daily=daily, now=effective_now)
    total = ctx.user_total_quota(cfg)
    expiry = ctx.user_expiry_state(cfg, today=effective_now.date())
    base_quota = ctx.base_quota_bytes(cfg)
    token = cfg.get('sub_token', '')
    spark = None
    if daily is not None:
        spark = ctx.daily_window_for_user(
            user,
            daily,
            days=30,
            today=effective_now.date(),
        )
    return {
        'user': user,
        'tx': tx,
        'rx': rx,
        'used': used,
        'total': total,
        'percent': ctx.pct(used, total),
        'online': int(online.get(user, 0) or 0),
        'revision': ctx.user_config_revision(cfg),
        'disabled': bool(cfg.get('disabled')),
        'max_devices': ctx.configured_max_devices(cfg),
        'base_quota_gb': (int(round(base_quota / 1024 / 1024 / 1024)) if base_quota > 0 else 0),
        'quota_extra_gb': ctx.quota_extra_gb(cfg),
        'metered': user_compat.is_metered(cfg),
        'tuic_enabled': user_compat.tuic_enabled(cfg),
        'expires_at': expiry['expires_at'],
        'expired': expiry['expired'],
        'expiry_label': expiry['label'] if expiry['expires_at'] else '',
        'note': str(cfg.get('note') or ''),
        'landing_isp': user_compat.landing_field(cfg, 'landing_isp'),
        'landing_region': user_compat.landing_field(cfg, 'landing_region'),
        'landing_note': user_compat.landing_field(cfg, 'landing_note'),
        'landing_ip': user_compat.landing_field(cfg, 'landing_ip'),
        'panel_url': f'{base_url}/panel/{user}?token={token}',
        'subscription_url': f'{base_url}/sub/{user}?token={token}',
        'spark': spark,
    }


def build_page(ctx, base_url):
    """Build the complete allowlisted overview bootstrap from one state snapshot."""
    users = ctx.load_json(ctx.USERS_FILE, {})
    landing_registry = ctx._landing_registry_or_empty()
    online = ctx.load_json(ctx.ONLINE_FILE, {})
    now = ctx.local_now()
    daily = ctx.load_json(ctx.USAGE_DAILY_FILE, {})
    user_rows = [
        build_user(ctx, user, cfg, online, base_url, daily=daily, now=now)
        for user, cfg in users.items()
    ]
    total_used = sum(user['used'] for user in user_rows)
    total_used += int(ctx.preserved_raw_for_cycle(now=now) * ctx.current_display_multiplier())
    settlement_day = ctx.get_settlement_day()
    cycle_length = ctx.get_cycle_length_days()
    cycle_start = ctx.cycle_start_for(now)
    cycle_end = cycle_start + timedelta(days=cycle_length - 1)
    cycle_day = (now.date() - cycle_start.date()).days + 1
    cycle_range = (
        f'{cycle_start.strftime("%m/%d")} → {cycle_end.strftime("%m/%d")}'
        f' · 第 {cycle_day}/{cycle_length} 天'
    )
    landing_options = [
        {'id': str(node['id']), 'name': node['name']}
        for node in ctx._enabled_landing_public_nodes(landing_registry)
    ]
    return {
        'cycle': {
            'key': ctx.month_key(now),
            'total_used': total_used,
            'range': cycle_range,
            'settlement_day': settlement_day,
            'length_days': cycle_length,
            'length_min': ctx.CYCLE_LENGTH_MIN,
            'length_max': ctx.CYCLE_LENGTH_MAX,
        },
        'users': user_rows,
        'landing_options': landing_options,
    }
