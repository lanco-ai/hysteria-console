"""Structured template and routing-rule read API contracts."""

from fastapi.testclient import TestClient
from web_api import create_app


class StubConfigServices:
    def read_admin_config(self, *, headers, path):
        del headers, path
        return {'config': {'proxies': [], 'proxy-groups': [], 'rules': []}, 'revision': 'a' * 64}

    def read_admin_rules(self, *, headers, path):
        del headers, path
        return {'rules': ['MATCH,DIRECT'], 'revision': 'b' * 64}

    def submit_template_config(self, *, headers, path, form, client_address):
        del headers, path, form, client_address
        return {'ok': True, 'revision': 'c' * 64}

    def submit_template_rules(self, *, headers, path, form, client_address):
        del headers, path, form, client_address
        return {'ok': True, 'revision': 'd' * 64}


def test_config_route_returns_revision_and_editable_template_shape():
    with TestClient(create_app(StubConfigServices())) as client:
        response = client.get('/api/v1/admin/config')

    assert response.status_code == 200
    assert set(response.json()) == {'config', 'revision'}
    assert response.json()['revision'] == 'a' * 64


def test_rules_route_returns_revision_and_ordered_rules():
    with TestClient(create_app(StubConfigServices())) as client:
        response = client.get('/api/v1/admin/rules')

    assert response.status_code == 200
    assert set(response.json()) == {'rules', 'revision'}
    assert response.json()['rules'] == ['MATCH,DIRECT']


def test_config_save_route_returns_new_revision():
    with TestClient(create_app(StubConfigServices())) as client:
        response = client.post(
            '/api/v1/admin/config/save',
            data={
                'config_json': '{"proxies": [], "proxy-groups": [], "rules": []}',
                'template_revision': 'a' * 64,
            },
            headers={'Origin': 'http://testserver'},
        )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'revision': 'c' * 64}


def test_rules_save_route_returns_new_revision():
    with TestClient(create_app(StubConfigServices())) as client:
        response = client.post(
            '/api/v1/admin/rules/save',
            data={'rules_raw': 'MATCH,DIRECT', 'template_revision': 'b' * 64},
            headers={'Origin': 'http://testserver'},
        )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'revision': 'd' * 64}
