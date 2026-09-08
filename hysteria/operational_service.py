"""Bounded operational calls, fail-closed actions and multiplier policy updates."""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import alerts
import cost_calibrator
import state_store
import static_access


@dataclass(frozen=True)
class OperationalService:
    CredentialActionResult: type
    DISPLAY_MULTIPLIER_STATE_FILE: Path
    HY_API_SECRET_FALLBACK: str
    HY_API_SECRET_FILE: Path
    HY_API_SECRET_PLACEHOLDER: str
    HY_KICK_MAX_RESPONSE_BYTES: int
    HY_KICK_TIMEOUT_SECONDS: float
    META_FILE: Path
    MULTIPLIER_AUTO_POLICY_FILE: Path
    USAGE_DAILY_FILE: Path
    USAGE_FILE: Path
    USERS_FILE: Path
    _using_live_core_state: Callable[..., object]
    current_display_multiplier: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    parse_int_field: Callable[..., object]
    summarize_cost_calibration: Callable[..., object]
    secret_provider: Callable[[], str]
    restart_provider: Callable[[], object]

    def _normalize_service_action(self, service, raw):
        if isinstance(raw, static_access.ServiceActionResult):
            return raw
        ok = raw is True
        return static_access.ServiceActionResult(
            service=service,
            action='stop_fail_closed',
            attempted=True,
            ok=ok,
            effect_confirmed=ok,
            marker_persisted=ok,
            code='stopped' if ok else 'unconfirmed',
            retryable=not ok,
        )

    def _fail_closed_static_access(self, reason):
        """Immediately revoke file-backed proxy auth when core state is unsafe."""
        live = self._using_live_core_state()
        outcomes = {}
        for service in static_access.SERVICES:
            raw = static_access.stop_fail_closed(
                service,
                reason=reason,
                live=live,
            )
            outcomes[service] = self._normalize_service_action(service, raw)
        return outcomes

    def _state_failure_requires_static_stop(self, exc, *, post_path=''):
        del post_path
        if isinstance(exc, state_store.CriticalStateUnavailable):
            return True
        if not isinstance(
            exc,
            state_store.AtomicReplaceDurabilityUncertain,
        ):
            return False
        core_paths = {
            str(Path(path))
            for path in (
                self.USERS_FILE,
                self.META_FILE,
                self.USAGE_FILE,
                self.USAGE_DAILY_FILE,
            )
        }
        return str(Path(exc.path)) in core_paths

    def _static_stop_confirmed(self, outcomes):
        return (
            isinstance(outcomes, dict)
            and len(outcomes) == len(static_access.SERVICES)
            and all(getattr(outcome, 'effect_confirmed', False) for outcome in outcomes.values())
        )

    def get_hy_api_secret(self):
        """Read the hysteria API auth secret at runtime from /root/hysteria/api_secret.
        Falls back to the (possibly sed-substituted) module-level constant so existing
        deploys keep working without re-rendering. Reading at request time means
        a deploy that updates only the secret file takes effect immediately, and
        a `git pull` that resets the source file to the literal placeholder no
        longer causes 401s in the cron tick."""
        try:
            with open(self.HY_API_SECRET_FILE, 'r', encoding='utf-8') as f:
                v = f.read().strip()
            if v and v != self.HY_API_SECRET_PLACEHOLDER:
                return v
        except OSError:
            pass
        return self.HY_API_SECRET_FALLBACK

    def hy_kick(self, usernames):
        """Force-disconnect active hysteria sessions for the given usernames."""
        if not usernames:
            return self.CredentialActionResult(
                action='hysteria_kick',
                target='',
                attempted=False,
                ok=True,
                code='not_needed',
                retryable=False,
            )
        target = ','.join(sorted(str(user) for user in usernames))
        connection = None
        try:
            body = json.dumps(list(usernames)).encode('utf-8')
            deadline = time.monotonic() + self.HY_KICK_TIMEOUT_SECONDS
            connection = http.client.HTTPConnection(
                '127.0.0.1',
                25413,
                timeout=self.HY_KICK_TIMEOUT_SECONDS,
            )
            connection.request(
                'POST',
                '/kick',
                body=body,
                headers={
                    'Authorization': self.secret_provider(),
                    'Content-Type': 'application/json',
                    'Content-Length': str(len(body)),
                },
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('kick request deadline exceeded')
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            response = connection.getresponse()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('kick response deadline exceeded')
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            response_body = response.read(self.HY_KICK_MAX_RESPONSE_BYTES + 1)
            if len(response_body) > self.HY_KICK_MAX_RESPONSE_BYTES:
                return self.CredentialActionResult(
                    action='hysteria_kick',
                    target=target,
                    attempted=True,
                    ok=False,
                    code='response_too_large',
                    retryable=True,
                )
            status = int(getattr(response, 'status', 0) or 0)
            if 200 <= status < 300:
                return self.CredentialActionResult(
                    action='hysteria_kick',
                    target=target,
                    attempted=True,
                    ok=True,
                    code='accepted',
                    retryable=False,
                )
            return self.CredentialActionResult(
                action='hysteria_kick',
                target=target,
                attempted=True,
                ok=False,
                code='unexpected_status',
                retryable=True,
            )
        except Exception as exc:
            return self.CredentialActionResult(
                action='hysteria_kick',
                target=target,
                attempted=True,
                ok=False,
                code=type(exc).__name__,
                retryable=True,
            )
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    def _fire_test_alert(self, cfg, actor):
        """Dispatch a synthetic alert on a background daemon thread so a slow or
        unreachable channel never blocks the admin request thread. SSRF note: the
        webhook URL is operator-supplied (admin-equivalent trust); no allowlisting
        by design. Returns the started thread (handy for tests)."""
        event = {
            'kind': 'test',
            'user': actor or 'admin',
            'details': {'note': '来自管理面板的测试告警'},
        }
        t = threading.Thread(
            target=alerts.dispatch,
            args=(event,),
            kwargs={'config': cfg},
            daemon=True,
        )
        t.start()
        return t

    def restart_subscription_async(self):
        try:
            subprocess.Popen(
                [
                    'systemd-run',
                    '--no-block',
                    '--on-active=2s',
                    '--unit',
                    f'hy2-subscription-restart-{int(time.time())}',
                    'systemctl',
                    'restart',
                    'hysteria-subscription.service',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def apply_suggested_display_multiplier(self, *, actor='admin', now=None):
        now = now or self.local_now()
        previous_multiplier = self.current_display_multiplier()
        summary = self.summarize_cost_calibration(now=now)
        policy = cost_calibrator.load_auto_policy(self.MULTIPLIER_AUTO_POLICY_FILE)
        decision = cost_calibrator.evaluate_multiplier_candidate(
            summary,
            previous_multiplier,
            policy,
            runtime_state=self.load_json(self.DISPLAY_MULTIPLIER_STATE_FILE, {}),
            now=now,
            manual=True,
        )
        if decision.get('reason') == 'low_confidence':
            return 'multiplier_low_confidence'
        if decision.get('reason') == 'delta_too_large':
            return 'multiplier_delta_too_large'
        if not decision.get('apply'):
            return 'multiplier_invalid'
        cost_calibrator.write_multiplier_state(
            self.DISPLAY_MULTIPLIER_STATE_FILE,
            multiplier=decision['candidate'],
            previous_multiplier=previous_multiplier,
            summary=summary,
            mode=policy.get('mode', 'total'),
            actor=actor or 'admin',
            now=now,
            auto=False,
        )
        self.restart_provider()
        return 'multiplier_applied'

    def save_multiplier_auto_policy_from_form(self, form):
        policy = cost_calibrator.load_auto_policy(self.MULTIPLIER_AUTO_POLICY_FILE)
        policy.update(
            {
                'enabled': 'enabled' in form,
                'mode': (form.get('mode') or ['total'])[0],
                'min_confidence': (form.get('min_confidence') or ['medium'])[0],
                'max_delta_percent': self.parse_int_field(
                    (form.get('max_delta_percent') or ['25'])[0], 25, 1, 100
                ),
                'min_delta_percent': self.parse_int_field(
                    (form.get('min_delta_percent') or ['3'])[0], 3, 0, 50
                ),
                'cooldown_hours': self.parse_int_field(
                    (form.get('cooldown_hours') or ['24'])[0], 24, 1, 168
                ),
            }
        )
        cost_calibrator.save_auto_policy(policy, self.MULTIPLIER_AUTO_POLICY_FILE)
