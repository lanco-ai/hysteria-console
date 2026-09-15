"""Structured residential-egress mutation contracts."""

from types import SimpleNamespace

from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices, LoginRequired


class StubLandingServices:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def read_admin_landing(self, *, headers, path):
        del headers, path
        return {
            'ts': '2026-09-15T10:30:00+08:00',
            'revision': 'a' * 64,
            'nodes': [],
            'users': [],
        }

    def submit_landing_operation(
        self, *, headers, path, form, client_address, action, request_user_revision=''
    ):
        del headers, path, client_address, request_user_revision
        self.calls.append((action, form))
        if self.result is not None:
            return self.result
        return {'ok': True, 'action': action, 'revision': 'b' * 64}


def test_landing_mutation_routes_forward_actions():
    services = StubLandingServices()
    headers = {'Origin': 'http://testserver'}
    with TestClient(create_app(services)) as client:
        responses = [
            client.post('/api/v1/admin/landing-egresses/save', headers=headers, data={'id': 'a'}),
            client.post('/api/v1/admin/landing-egresses/delete', headers=headers, data={'id': 'a'}),
            client.post('/api/v1/admin/landing-egresses/check', headers=headers, data={'id': 'a'}),
            client.post(
                '/api/v1/admin/landing-egresses/access', headers=headers, data={'user': 'alice'}
            ),
            client.post(
                '/api/v1/user/landing-egress/select', headers=headers, data={'egress_id': 'a'}
            ),
        ]

    assert [response.status_code for response in responses] == [200] * 5
    assert [response.json()['action'] for response in responses] == [
        'save',
        'delete',
        'check',
        'access',
        'select',
    ]
    assert [action for action, _form in services.calls] == [
        'save',
        'delete',
        'check',
        'access',
        'select',
    ]


def test_landing_mutation_maps_conflict_and_forbidden():
    conflict = StubLandingServices({'ok': False, 'action': 'save', 'error': 'revision_conflict'})
    forbidden = StubLandingServices({'ok': False, 'action': 'select', 'error': 'forbidden'})
    with TestClient(create_app(conflict)) as client:
        conflict_response = client.post(
            '/api/v1/admin/landing-egresses/save',
            headers={'Origin': 'http://testserver'},
            data={},
        )
    with TestClient(create_app(forbidden)) as client:
        forbidden_response = client.post(
            '/api/v1/user/landing-egress/select',
            headers={'Origin': 'http://testserver'},
            data={},
        )

    assert conflict_response.status_code == 409
    assert conflict_response.json() == {
        'ok': False,
        'action': 'save',
        'error': 'revision_conflict',
    }
    assert forbidden_response.status_code == 403
    assert forbidden_response.json() == {
        'ok': False,
        'action': 'select',
        'error': 'forbidden',
    }


def test_landing_mutation_requires_session():
    class Unauthorized(StubLandingServices):
        def submit_landing_operation(self, **kwargs):
            del kwargs
            raise LoginRequired

    with TestClient(create_app(Unauthorized())) as client:
        response = client.post(
            '/api/v1/admin/landing-egresses/check',
            headers={'Origin': 'http://testserver'},
            data={'id': 'a'},
        )

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_legacy_landing_adapter_captures_redirect_and_retry_after():
    class ServiceModule:
        state_store = SimpleNamespace(StateStoreError=RuntimeError)
        landing_write_routes = SimpleNamespace()

        @staticmethod
        def request_multiplier_snapshot(function):
            return function

        @staticmethod
        def _landing_write_routes_context():
            return object()

        @staticmethod
        def _landing_registry_or_empty():
            return {'nodes': {}}

        @staticmethod
        def content_revision(_registry):
            return 'c' * 64

    def redirect_route(handler, _context, **_kwargs):
        handler.redirect('/admin/landing-egresses?msg=saved', status=303)
        return True

    ServiceModule.landing_write_routes.handle_write = redirect_route
    services = LegacyPanelServices(ServiceModule)
    result = services.submit_landing_operation(
        headers={},
        path='/api/v1/admin/landing-egresses/save',
        form={},
        client_address=('', 0),
        action='save',
    )
    assert result == {'ok': True, 'action': 'save', 'revision': 'c' * 64}

    def retry_route(handler, _context, **_kwargs):
        handler.send_response_body(429, extra_headers={'Retry-After': '7'})
        return True

    ServiceModule.landing_write_routes.handle_write = retry_route
    result = services.submit_landing_operation(
        headers={},
        path='/user/landing-egress/select',
        form={},
        client_address=('', 0),
        action='select',
    )
    assert result == {
        'ok': False,
        'action': 'select',
        'error': 'rate_limited',
        'retry_after': 7,
    }
