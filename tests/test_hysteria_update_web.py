"""Admin Web contracts for the Hysteria updater."""

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import http.client
import json
import subprocess

import subscription_service as ss
import state_store


@contextmanager
def _running_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ss.Handler)
    thread = __import__("threading").Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _configure_admin(tmp_path, monkeypatch):
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps({
            "admin_user": "admin",
            "admin_pass_hash": "unused-but-present",
            "admin_token": "admin-token",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(ss, "META_FILE", meta)


def _post(server, path, *, headers=None):
    conn = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5,
    )
    request_headers = {"Host": "panel.test"}
    if headers:
        request_headers.update(headers)
    conn.request("POST", path, body="", headers=request_headers)
    response = conn.getresponse()
    body = response.read()
    result = (
        response.status,
        {key.lower(): value for key, value in response.getheaders()},
        body,
    )
    conn.close()
    return result


def _get(server, path, *, headers=None):
    conn = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5,
    )
    request_headers = {"Host": "panel.test"}
    if headers:
        request_headers.update(headers)
    conn.request("GET", path, headers=request_headers)
    response = conn.getresponse()
    body = response.read()
    result = (
        response.status,
        {key.lower(): value for key, value in response.getheaders()},
        body,
    )
    conn.close()
    return result


def test_check_route_uses_shared_updater_lock_helper(tmp_path, monkeypatch):
    """Calling check_latest/record_check directly would return the wrong flash."""
    _configure_admin(tmp_path, monkeypatch)
    called = {"helper": False}

    def busy_check_and_record(**_kwargs):
        called["helper"] = True
        raise state_store.LockTimeout("busy")

    monkeypatch.setattr(
        ss.hysteria_update,
        "check_and_record",
        busy_check_and_record,
        raising=False,
    )
    monkeypatch.setattr(
        ss.hysteria_update,
        "check_latest",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("route bypassed check_and_record")
        ),
    )

    with _running_server() as server:
        status, headers, _body = _post(
            server,
            "/admin/hysteria-update/check?token=admin-token",
        )

    assert called["helper"] is True
    assert status == 302
    assert headers["location"].endswith("msg=err:hysteria_update_busy")


def test_check_route_returns_ajax_json(tmp_path, monkeypatch):
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ss.hysteria_update,
        "check_and_record",
        lambda **_kwargs: {
            "current": "v2.11.0",
            "latest": "app/v2.12.2",
            "update_available": True,
        },
    )

    with _running_server() as server:
        status, _headers, body = _post(
            server,
            "/admin/hysteria-update/check?token=admin-token",
            headers={"Accept": "application/json"},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload == {
        "ok": True,
        "status": "checked",
        "current": "v2.11.0",
        "latest": "app/v2.12.2",
        "update_available": True,
        "pending": False,
    }


def test_apply_route_schedules_worker_and_returns_ajax_immediately(
    tmp_path, monkeypatch,
):
    _configure_admin(tmp_path, monkeypatch)
    called = {"scheduled": 0}

    def schedule():
        called["scheduled"] += 1
        return {
            "status": "scheduled",
            "reason": "",
            "ts": "2026-08-27T12:00:00+00:00",
        }

    monkeypatch.setattr(ss.hysteria_update, "schedule_apply_async", schedule)
    monkeypatch.setattr(
        ss.hysteria_update,
        "apply_update",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP route ran synchronous apply")
        ),
    )

    with _running_server() as server:
        status, _headers, body = _post(
            server,
            "/admin/hysteria-update/apply?token=admin-token",
            headers={"Accept": "application/json"},
        )

    assert called["scheduled"] == 1
    assert status == 202
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["status"] == "scheduled"
    assert payload["pending"] is True


def test_update_status_endpoint_is_authenticated_and_secret_free(
    tmp_path, monkeypatch,
):
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ss.hysteria_update,
        "public_status",
        lambda: {
            "ok": True,
            "status": "applying",
            "reason": "",
            "ts": "2026-08-27T12:00:00+00:00",
            "pending": True,
        },
    )

    with _running_server() as server:
        unauthorized, _headers, _body = _get(
            server, "/admin/hysteria-update/status.json",
        )
        status, headers, body = _get(
            server,
            "/admin/hysteria-update/status.json?token=admin-token",
        )

    assert unauthorized == 401
    assert status == 200
    assert headers["cache-control"] == "no-store"
    payload = json.loads(body.decode("utf-8"))
    assert payload["pending"] is True
    assert "error" not in payload


def test_health_page_loads_updater_ajax_with_progressive_form_fallback(
    tmp_path, monkeypatch,
):
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, '_render_health_top_kpis', lambda: {})
    monkeypatch.setattr(ss, '_render_health_cards', lambda: '')
    monkeypatch.setattr(ss, 'render_line_radar', lambda: '')
    monkeypatch.setattr(ss, 'render_cost_calibrator', lambda: '')
    monkeypatch.setattr(
        ss.hysteria_update, 'render_history',
        lambda: (
            '<form method="post" action="/admin/hysteria-update/check" '
            'data-action="hysteria-update-check"><button type="submit">check</button></form>'
            '<form method="post" action="/admin/hysteria-update/apply" '
            'data-action="hysteria-update-apply" data-confirm="confirm">'
            '<button type="submit">apply</button></form>'
        ),
    )

    page = ss.render_health('panel.test')

    assert '/static/admin-poll.js?' in page
    assert 'method="post" action="/admin/hysteria-update/check"' in page
    assert 'method="post" action="/admin/hysteria-update/apply"' in page
    assert 'data-action="hysteria-update-check"' in page
    assert 'data-action="hysteria-update-apply"' in page


def test_updater_ajax_consumes_shell_confirmation_without_second_prompt():
    shell = ss.render_admin_shell('health', 'Health', '<main></main>')
    js = ss.ADMIN_POLL_JS_BYTES.decode('utf-8')

    assert '__hy2Confirmed' in shell
    assert '__hy2Confirmed' in js
    assert 'ev.preventDefault()' in js


def test_updater_ajax_does_not_submit_event_already_cancelled_by_shell():
    source = ss.ADMIN_POLL_JS_BYTES.decode('utf-8')
    harness = r'''
const vm = require('vm');
const submitHandlers = [];
let confirms = 0;
let fetches = 0;
const document = {
  hidden: false,
  querySelector: () => null,
  querySelectorAll: () => [],
  getElementById: () => null,
  addEventListener: (name, fn) => { if (name === 'submit') submitHandlers.push(fn); }
};
const window = {
  location: { href: 'https://panel.test/admin/health', assign: () => {}, reload: () => {} },
  addEventListener: () => {},
  console: { warn: () => {} }
};
const context = {
  document, window, navigator: {}, URL, URLSearchParams,
  Map, Set, Promise,
  confirm: () => { confirms++; return true; },
  fetch: () => { fetches++; return Promise.reject(new Error('unexpected fetch')); },
  FormData: function(){ this.forEach = function(){}; },
  AbortController: undefined,
  setTimeout: () => 1,
  clearTimeout: () => {}
};
vm.createContext(context);
vm.runInContext(SOURCE, context);
if (submitHandlers.length !== 1) throw new Error('submit handler missing');
const form = {
  tagName: 'FORM', dataset: { action: 'hysteria-update-apply' },
  action: '/admin/hysteria-update/apply', __pendingSubmitter: null
};
submitHandlers[0]({
  target: form, submitter: null, defaultPrevented: true,
  preventDefault: () => {}
});
if (confirms !== 0 || fetches !== 0) {
  throw new Error('cancelled submit continued: confirms=' + confirms + ' fetches=' + fetches);
}
'''.replace('SOURCE', json.dumps(source))

    result = subprocess.run(
        ['node', '-e', harness], capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
