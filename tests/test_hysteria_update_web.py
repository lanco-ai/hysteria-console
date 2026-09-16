"""Admin Web contracts for the Hysteria updater."""

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import http.client
import json

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


def test_health_snapshot_requires_auth_and_returns_all_regions(tmp_path, monkeypatch):
    _configure_admin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ss,
        '_build_health_read_snapshot',
        lambda: {'rows': '<tr data-health="Hysteria"></tr>', 'kpis': 'ok', 'update': 'history'},
    )
    with _running_server() as server:
        status, _, _ = _get(server, '/admin/health.fragment?snapshot=1')
        assert status == 401
        status, headers, body = _get(server, '/admin/health.fragment?snapshot=1&token=admin-token')
    assert status == 200
    assert 'application/json' in headers['content-type']
    assert json.loads(body) == {'rows': '<tr data-health="Hysteria"></tr>', 'kpis': 'ok', 'update': 'history'}


def test_every_sidebar_entry_has_active_navigation_and_unique_main():
    for key, path, title, _icon in ss._SIDEBAR_NAV:
        page = ss.render_admin_shell(key, title, '<p>页面内容</p>')
        assert f'href="{path}" class="sidebar-link active" aria-current="page"' in page
        assert page.count('id="main-content"') == 1
        assert 'id="sidebar-collapse"' in page
        assert 'id="sidebar-close"' in page


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

    assert '/static/admin-poll.js' not in page
    assert 'method="post" action="/admin/hysteria-update/check"' in page
    assert 'method="post" action="/admin/hysteria-update/apply"' in page
    assert 'data-action="hysteria-update-check"' in page
    assert 'data-action="hysteria-update-apply"' in page
