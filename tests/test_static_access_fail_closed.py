import json
from types import SimpleNamespace

import static_access
import subscription_service as ss
import traffic_limiter as tl


class _Runner:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(returncode=self.returncode)


class _SequenceRunner:
    def __init__(self, *returncodes):
        self.returncodes = iter(returncodes)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(returncode=next(self.returncodes))


class _FailFirstStopStatefulRunner:
    def __init__(self):
        self.active = True
        self.stop_attempts = 0
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        action = command[1]
        if action == "stop":
            self.stop_attempts += 1
            if self.stop_attempts == 1:
                # The failed command leaves the old process and config active.
                return SimpleNamespace(returncode=1)
            self.active = False
            return SimpleNamespace(returncode=0)
        if action == "start":
            assert self.active is False
            self.active = True
            return SimpleNamespace(returncode=0)
        if action == "is-active":
            return SimpleNamespace(returncode=0 if self.active else 3)
        raise AssertionError(f"unexpected command: {command}")


def test_stop_persists_marker_before_service_command(tmp_path):
    runner = _Runner()

    result = static_access.stop_fail_closed(
        static_access.XRAY_SERVICE,
        reason=RuntimeError("sensitive detail must not be persisted"),
        live=True,
        state_dir=tmp_path,
        runner=runner,
    )
    assert result.ok is True
    assert result.effect_confirmed is True
    assert result.marker_persisted is True
    assert result.code == "stopped"

    marker = static_access.marker_path(
        static_access.XRAY_SERVICE, state_dir=tmp_path
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["service"] == static_access.XRAY_SERVICE
    assert payload["reason_type"] == "RuntimeError"
    assert "sensitive detail" not in marker.read_text(encoding="utf-8")
    assert runner.calls[0][0] == [
        "systemctl", "stop", static_access.XRAY_SERVICE,
    ]


def test_non_live_state_can_never_touch_systemd_or_marker(tmp_path):
    runner = _Runner()

    result = static_access.stop_fail_closed(
        static_access.TUIC_SERVICE,
        reason=RuntimeError("bad test state"),
        live=False,
        state_dir=tmp_path,
        runner=runner,
    )
    assert result.ok is False
    assert result.attempted is False
    assert result.code == "not_live"

    assert runner.calls == []
    assert list(tmp_path.iterdir()) == []


def test_recovery_starts_only_marked_service_and_clears_after_success(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(static_access.time, "sleep", lambda _seconds: None)
    stop_runner = _Runner()
    start_runner = _Runner()
    service = static_access.TUIC_SERVICE
    assert static_access.stop_fail_closed(
        service,
        reason=RuntimeError("bad state"),
        live=True,
        state_dir=tmp_path,
        runner=stop_runner,
    )

    assert static_access.recover_if_pending(
        service,
        live=True,
        state_dir=tmp_path,
        runner=start_runner,
    ) is True
    assert start_runner.calls[0][0] == ["systemctl", "stop", service]
    assert start_runner.calls[1][0] == ["systemctl", "start", service]
    assert len(start_runner.calls) == (
        2 + static_access.RECOVERY_READINESS_STABILITY_PROBES
    )
    assert not static_access.marker_path(
        service, state_dir=tmp_path
    ).exists()

    start_runner.calls.clear()
    assert static_access.recover_if_pending(
        service,
        live=True,
        state_dir=tmp_path,
        runner=start_runner,
    ) is False
    assert start_runner.calls == []


def test_failed_recovery_retains_marker_for_next_tick(tmp_path):
    service = static_access.XRAY_SERVICE
    static_access.stop_fail_closed(
        service,
        reason=RuntimeError("bad state"),
        live=True,
        state_dir=tmp_path,
        runner=_Runner(),
    )

    assert static_access.recover_if_pending(
        service,
        live=True,
        state_dir=tmp_path,
        runner=_Runner(returncode=1),
    ) is False
    assert static_access.marker_path(
        service, state_dir=tmp_path
    ).exists()


def test_recovery_readiness_failure_retains_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(static_access.time, "sleep", lambda _seconds: None)
    service = static_access.XRAY_SERVICE
    static_access.stop_fail_closed(
        service,
        reason=RuntimeError("bad state"),
        live=True,
        state_dir=tmp_path,
        runner=_Runner(),
    )
    runner = _SequenceRunner(0, 0, 1)

    assert static_access.recover_if_pending(
        service,
        live=True,
        state_dir=tmp_path,
        runner=runner,
    ) is False
    assert runner.calls[2][0] == [
        "systemctl", "is-active", "--quiet", service,
    ]
    assert static_access.marker_path(
        service, state_dir=tmp_path
    ).exists()


def test_failed_original_stop_cannot_be_mistaken_for_recovery(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(static_access.time, "sleep", lambda _seconds: None)
    service = static_access.XRAY_SERVICE
    runner = _FailFirstStopStatefulRunner()
    original_stop = static_access.stop_fail_closed(
        service,
        reason=RuntimeError("bad state"),
        live=True,
        state_dir=tmp_path,
        runner=runner,
    )
    assert original_stop.effect_confirmed is False
    assert runner.active is True
    assert static_access.marker_path(
        service, state_dir=tmp_path
    ).exists()

    assert static_access.recover_if_pending(
        service,
        live=True,
        state_dir=tmp_path,
        runner=runner,
    ) is True
    assert runner.active is True
    assert [call[0][1] for call in runner.calls[:3]] == [
        "stop",
        "stop",
        "start",
    ]


def test_recovery_pre_stop_failure_retains_marker_and_never_starts(
    tmp_path,
):
    service = static_access.TUIC_SERVICE
    static_access.stop_fail_closed(
        service,
        reason=RuntimeError("bad state"),
        live=True,
        state_dir=tmp_path,
        runner=_Runner(),
    )
    runner = _Runner(returncode=1)

    assert static_access.recover_if_pending(
        service,
        live=True,
        state_dir=tmp_path,
        runner=runner,
    ) is False
    assert [call[0] for call in runner.calls] == [
        ["systemctl", "stop", service],
    ]
    assert static_access.marker_path(
        service, state_dir=tmp_path
    ).exists()


def test_subscription_state_failure_revokes_both_static_proxies(monkeypatch):
    calls = []
    monkeypatch.setattr(ss, "_using_live_core_state", lambda: True)
    monkeypatch.setattr(
        ss.static_access,
        "stop_fail_closed",
        lambda service, *, reason, live: calls.append(
            (service, type(reason).__name__, live)
        ),
    )

    ss._fail_closed_static_access(RuntimeError("core unavailable"))

    assert calls == [
        (static_access.XRAY_SERVICE, "RuntimeError", True),
        (static_access.TUIC_SERVICE, "RuntimeError", True),
    ]


def test_successful_exact_plan_attempts_marked_service_recovery(monkeypatch):
    recovered = []
    monkeypatch.setattr(tl, "_using_live_core_state", lambda: True)
    monkeypatch.setattr(
        tl.xray_config, "apply_user_plan", lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        tl.tuic_config, "sync_user_plan", lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        tl.static_access,
        "recover_if_pending",
        lambda service, *, live: recovered.append((service, live)),
    )

    assert tl._apply_static_access_plan({}, {}) == (False, False)
    assert recovered == [
        (tl.xray_config.RELOAD_SERVICE, True),
        (tl.tuic_config.RELOAD_SERVICE, True),
    ]
