"""Retired quota endpoints must not remain reachable through the admin shell."""

import pytest
import subscription_service as ss
from tests.test_reliability_regressions import _configure_state, _request, _running_server


@pytest.mark.parametrize('path', ['/admin/codex', '/admin/codex.json', '/static/codex-quota.js'])
def test_retired_quota_endpoints_are_unavailable(tmp_path, monkeypatch, path):
    _configure_state(tmp_path, monkeypatch)
    cookie = {'Cookie': f'sid={ss.create_session()}'}
    with _running_server() as server:
        response = _request(server, 'GET', path, headers=cookie)
    assert response.status == 404


def test_admin_navigation_has_no_retired_quota_link(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch)
    cookie = {'Cookie': f'sid={ss.create_session()}'}
    with _running_server() as server:
        response = _request(server, 'GET', '/admin', headers=cookie)
    assert response.status == 200
    assert b'/admin/codex' not in response.body
