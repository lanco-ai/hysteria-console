"""Billing-cycle storage and display accounting, independent of HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cycle as cycle_util
import user_compat
from timeutil import billing_cycle_key


@dataclass(frozen=True)
class BillingService:
    load_meta: Callable[..., object]
    local_now: Callable[..., object]
    meta_lock: Callable[..., object]
    save_json: Callable[..., object]
    load_json: Callable[..., object]
    current_display_multiplier: Callable[..., object]
    META_FILE: Path
    USAGE_DAILY_FILE: Path
    USAGE_HOURLY_FILE: Path
    USAGE_PRESERVED_FILE: Path

    def get_settlement_day(self):
        """Day-of-month when the billing cycle rolls over. Editable via /admin/cycle-config."""
        return cycle_util.settlement_day_from_meta(self.load_meta())

    def get_cycle_length_days(self):
        """Length of one billing cycle, in days. Editable via /admin/cycle-config.
        Cycles roll exactly every N days from `cycle_anchor_date` (or, if absent,
        from the most recent settlement_day on/before today)."""
        return cycle_util.cycle_length_from_meta(self.load_meta())

    def _settlement_anchor_date(self, now, settlement_day):
        """Most recent date with day-of-month == settlement_day, on/before now.date().
        Falls back through prev month / Feb edge cases."""
        return cycle_util.settlement_anchor_date(now, settlement_day)

    def _update_cycle_meta(self, day, length=None, *, now=None):
        """Update cycle settings without overwriting a concurrent admin rekey."""
        current_time = now or self.local_now()
        with self.meta_lock():
            meta = self.load_meta()
            meta['settlement_day'] = day
            if length is not None:
                meta['cycle_length_days'] = length
            meta['cycle_anchor_date'] = self._settlement_anchor_date(
                current_time,
                day,
            ).strftime('%Y-%m-%d')
            self.save_json(self.META_FILE, meta)
            return meta

    def get_cycle_anchor_date(self, now=None):
        """The anchor date (a settlement day in the past or today) that all N-day
        cycle blocks count from. Read from META_FILE if persisted, else derive
        from the current settlement_day. Storing the anchor keeps cycle boundaries
        stable across the inevitable jump that would otherwise happen each month
        when settlement_day recurs (e.g. with cycle_length=15, the most-recent-
        settlement-day-of-month anchor would skip cycles)."""
        if now is None:
            now = self.local_now()
        meta = self.load_meta()
        return cycle_util.cycle_anchor_date(now, meta)

    def cycle_start_for(self, now, day=None, length=None, anchor=None):
        """Datetime at 00:00 local of the current cycle's start.

        For cycle_length_days==30 (default) the result matches the pre-existing
        calendar-month behaviour as long as the anchor is the most recent
        settlement_day. For shorter/longer N, cycles roll exactly every N days
        from the anchor — they intentionally do not re-align to calendar months."""
        meta = self.load_meta()
        return cycle_util.cycle_start_for(now, day=day, length=length, anchor=anchor, meta=meta)

    def month_key(self, now=None):
        """Legacy cycle key (YYYY-MM) used as a dict key in usage.json. Cycle reads
        are now derived from usage_daily.json (see _cycle_days), so this key only
        needs to round-trip with traffic_limiter.billing_month_key; it does not
        drive the displayed cycle range."""
        if now is None:
            now = self.local_now()
        return billing_cycle_key(now, self.get_settlement_day())

    def _cycle_days(self, now):
        """List of YYYY-MM-DD date keys covered by the current cycle, oldest first.
        Capped at today (future days in a cycle aren't displayed/summed)."""
        return cycle_util.cycle_days(now, meta=self.load_meta())

    def _zero_cycle_daily_hourly_for(self, uids, *, now):
        """Zero each user's daily/hourly entries within the current cycle. Caller
        must hold usage_lock. Keeps the cycle-bucket reset in usage.json consistent
        with usage_daily.json/usage_hourly.json, so post-reset displays read 0
        instead of the pre-reset accumulated values."""
        uids = list(uids)
        if not uids:
            return
        days = set(self._cycle_days(now))
        cycle_start = self.cycle_start_for(now)
        hour_cutoff = cycle_start.strftime('%Y-%m-%dT%H')

        daily = self.load_json(self.USAGE_DAILY_FILE, {})
        changed_daily = False
        for dk in list(daily.keys()):
            if dk not in days:
                continue
            bucket = daily.get(dk) or {}
            for uid in uids:
                if uid in bucket:
                    bucket[uid] = {'tx': 0, 'rx': 0, 'total': 0}
                    changed_daily = True
        if changed_daily:
            self.save_json(self.USAGE_DAILY_FILE, daily)

        hourly = self.load_json(self.USAGE_HOURLY_FILE, {})
        changed_hourly = False
        for hk in list(hourly.keys()):
            if hk < hour_cutoff:
                continue
            bucket = hourly.get(hk) or {}
            for uid in uids:
                if uid in bucket:
                    bucket[uid] = {'tx': 0, 'rx': 0, 'total': 0}
                    changed_hourly = True
        if changed_hourly:
            self.save_json(self.USAGE_HOURLY_FILE, hourly)

    def _cycle_preserve_key(self, now):
        return self.cycle_start_for(now).date().isoformat()

    def preserved_raw_for_cycle(self, *, now):
        """Sum of raw bytes preserved (refreshed-not-cleared) for the current cycle.
        Used so 'refresh traffic' can zero a user's counter without shrinking the
        server's '本周期总流量' display."""
        data = self.load_json(self.USAGE_PRESERVED_FILE, {})
        bucket = data.get(self._cycle_preserve_key(now)) or {}
        total = 0
        for v in bucket.values():
            if isinstance(v, dict):
                total += int(v.get('total', 0))
            else:
                total += int(v or 0)
        return total

    def add_preserved_for_user(self, username, tx, rx, total, *, now):
        """Record `total` raw bytes against `username` under the current cycle's
        preserved bucket, additive across repeated refreshes. Caller holds usage_lock."""
        if total <= 0:
            return
        data = self.load_json(self.USAGE_PRESERVED_FILE, {})
        key = self._cycle_preserve_key(now)
        bucket = data.setdefault(key, {})
        cur = bucket.get(username) or {}
        if not isinstance(cur, dict):
            cur = {'tx': 0, 'rx': 0, 'total': int(cur or 0)}
        bucket[username] = {
            'tx': int(cur.get('tx', 0)) + int(tx),
            'rx': int(cur.get('rx', 0)) + int(rx),
            'total': int(cur.get('total', 0)) + int(total),
        }
        # GC: drop cycle keys older than the current one. Preserved bytes are a
        # display-only adjustment scoped to "this cycle" — past-cycle entries would
        # otherwise grow without bound across months.
        for k in list(data.keys()):
            if k < key:
                data.pop(k, None)
        self.save_json(self.USAGE_PRESERVED_FILE, data)

    def _cycle_raw_for_user(self, uid, daily, *, now):
        """Per-user raw cycle bytes derived from usage_daily.json. Returns (tx, rx, total).

        Daily is the canonical fine-grained source: `today`/`current hour` cards already
        read from daily/hourly, so deriving `cycle` from daily guarantees
        `cycle >= today >= current_hour` and avoids drift against the cycle bucket
        in `usage.json`, which is a separately-accumulated counter that can fall
        behind on file corruption, partial writes, or stale state."""
        tx = rx = total = 0
        for dk in self._cycle_days(now):
            entry = (daily.get(dk) or {}).get(uid)
            if isinstance(entry, dict):
                etx = int(entry.get('tx', 0))
                erx = int(entry.get('rx', 0))
                tx += etx
                rx += erx
                total += int(entry.get('total', etx + erx))
            else:
                total += int(entry or 0)
        return tx, rx, total

    def usage_for_user(self, username, usage_month=None, *, daily=None, now=None):
        """Per-user cycle raw bytes (tx, rx, total).

        The `usage_month` positional argument is kept for backward compat with
        legacy call sites that read the cycle bucket from usage.json; it is now
        ignored. Cycle value is always derived from usage_daily.json summed across
        days in the current cycle — see _cycle_raw_for_user for why."""
        if daily is None:
            daily = self.load_json(self.USAGE_DAILY_FILE, {})
        return self._cycle_raw_for_user(username, daily, now=now or self.local_now())

    def scaled_usage_for_user(self, username, usage_month=None, *, daily=None, now=None):
        tx, rx, total = self.usage_for_user(username, usage_month, daily=daily, now=now)
        m = self.current_display_multiplier()
        return int(tx * m), int(rx * m), int(total * m)

    def user_total_quota(self, user_cfg):
        return user_compat.total_quota_bytes(user_cfg)

    def base_quota_bytes(self, user_cfg):
        return int((user_cfg or {}).get('monthly_quota_bytes', 0) or 0)

    def quota_extra_gb(self, user_cfg):
        return int(round(user_compat.quota_extra_bytes(user_cfg) / 1024 / 1024 / 1024))

    def user_expiry_state(self, user_cfg, *, today=None):
        today = today or self.local_now().date()
        exp = user_compat.expiry_date(user_cfg)
        if exp is None:
            return {'expires_at': '', 'expired': False, 'days_left': None, 'label': '不限期'}
        days_left = (exp - today).days
        if days_left < 0:
            label = f'已过期 {abs(days_left)} 天'
        elif days_left == 0:
            label = '今日到期'
        else:
            label = f'{days_left} 天后到期'
        return {
            'expires_at': exp.strftime('%Y-%m-%d'),
            'expired': days_left < 0,
            'days_left': days_left,
            'label': label,
        }
