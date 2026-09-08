"""In-memory login failure accounting and atomic verification reservations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, ContextManager


@dataclass(frozen=True)
class LoginThrottle:
    failures: dict
    inflight: dict
    lock: ContextManager[object]
    clock: Callable[[], float]
    max_attempts: int
    window: int
    max_ips: int

    def _prune_failures_locked(self, ip, failures, now):
        times = [t for t in failures.get(ip, []) if now - t < self.window]
        if times:
            failures[ip] = times
        else:
            failures.pop(ip, None)
        return times

    def _is_rate_limited(self, ip, failures=None):
        failures = self.failures if failures is None else failures
        with self.lock:
            times = self._prune_failures_locked(ip, failures, self.clock())
            return len(times) >= self.max_attempts

    def _record_failure(self, ip, failures=None):
        failures = self.failures if failures is None else failures
        with self.lock:
            if ip not in failures and len(failures) >= self.max_ips:
                # Dicts preserve insertion order; evict the oldest tracked IP.
                oldest = next(iter(failures))
                failures.pop(oldest, None)
            failures.setdefault(ip, []).append(self.clock())

    def _begin_login_attempt(self, ip, failures=None):
        """Atomically reserve one of the allowed password-verification slots.

        Counting only after PBKDF2 verification lets a burst of concurrent
        requests all observe the same pre-failure state.  The short-lived
        reservation closes that race without holding the global mutex while the
        expensive hash runs.
        """
        failures = self.failures if failures is None else failures
        key = (id(failures), ip)
        with self.lock:
            times = self._prune_failures_locked(ip, failures, self.clock())
            inflight = int(self.inflight.get(key, 0))
            if len(times) + inflight >= self.max_attempts:
                return False
            self.inflight[key] = inflight + 1
            return True

    def _finish_login_attempt(self, ip, succeeded, failures=None):
        failures = self.failures if failures is None else failures
        key = (id(failures), ip)
        with self.lock:
            inflight = max(0, int(self.inflight.get(key, 0)) - 1)
            if inflight:
                self.inflight[key] = inflight
            else:
                self.inflight.pop(key, None)
            if succeeded is True:
                failures.pop(ip, None)
                return
            if succeeded is None:
                return
            if ip not in failures and len(failures) >= self.max_ips:
                failures.pop(next(iter(failures)), None)
            failures.setdefault(ip, []).append(self.clock())
