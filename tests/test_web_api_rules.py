"""Structured administrator routing-rule read and mutation contracts."""

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices, LoginRequired


class StubRulesServices:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def read_admin_rules(self, *, headers, path):
        del headers, path
        return {
            'rules': ['DOMAIN,example.com,DIRECT'],
            'revision': 'a' * 64,
            'packs': [
                {
                    'key': 'easyconnect',
                    'label': 'EasyConnect 直连',
                    'description': 'VPN 直连',
                }
            ],
            'users': ['alice'],
            'private': 'must not be exposed',
        }

    def submit_rules_operation(self, *, headers, path, form, client_address, action):
        del headers, path, client_address
        self.calls.append((action, form))
        if self.result is not None:
            return self.result
        return {
            'ok': True,
            'action': action,
            'revision': 'b' * 64,
            'user': (form.get('user') or [''])[0],
        }


def test_rules_read_includes_safe_pack_and_user_options():
    with TestClient(create_app(StubRulesServices())) as client:
        response = client.get('/api/v1/admin/rules')

    assert response.status_code == 200
    assert set(response.json()) == {'rules', 'revision', 'packs', 'users'}
    assert response.json()['packs'][0] == {
        'key': 'easyconnect',
        'label': 'EasyConnect 直连',
        'description': 'VPN 直连',
    }
    assert response.json()['users'] == ['alice']
    assert 'must not be exposed' not in response.text


def test_rules_mutation_routes_forward_action_and_form():
    services = StubRulesServices()
    headers = {'Origin': 'http://testserver'}
    with TestClient(create_app(services)) as client:
        add = client.post(
            '/api/v1/admin/rules/add',
            headers=headers,
            data={
                'rule_type': 'DOMAIN',
                'pattern': 'example.com',
                'action': 'DIRECT',
                'extra': '',
                'template_revision': 'a' * 64,
            },
        )
        delete = client.post(
            '/api/v1/admin/rules/delete',
            headers=headers,
            data={
                'index': '0',
                'expected_rule': 'DOMAIN,example.com,DIRECT',
                'template_revision': 'a' * 64,
            },
        )
        pack = client.post(
            '/api/v1/admin/rules/pack',
            headers=headers,
            data={
                'pack': 'easyconnect',
                'scope': 'user',
                'user': 'alice',
                'template_revision': 'a' * 64,
            },
        )

    assert add.status_code == 200 and add.json()['action'] == 'add'
    assert delete.status_code == 200 and delete.json()['action'] == 'delete'
    assert pack.status_code == 200 and pack.json()['action'] == 'pack'
    assert [action for action, _form in services.calls] == ['add', 'delete', 'pack']
    assert services.calls[2][1]['user'] == ['alice']


def test_rules_mutation_maps_conflict_to_409():
    services = StubRulesServices(
        {
            'ok': False,
            'action': 'delete',
            'error': 'revision_conflict',
        }
    )
    with TestClient(create_app(services)) as client:
        response = client.post(
            '/api/v1/admin/rules/delete',
            headers={'Origin': 'http://testserver'},
            data={'index': '0'},
        )

    assert response.status_code == 409
    assert response.json() == {
        'ok': False,
        'action': 'delete',
        'error': 'revision_conflict',
    }


def test_rules_mutation_requires_admin_session():
    class Unauthorized(StubRulesServices):
        def submit_rules_operation(self, **kwargs):
            del kwargs
            raise LoginRequired

    with TestClient(create_app(Unauthorized())) as client:
        response = client.post(
            '/api/v1/admin/rules/add',
            headers={'Origin': 'http://testserver'},
            data={},
        )

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_legacy_rules_adapter_builds_safe_metadata_and_adds_rule(tmp_path):
    users_file = tmp_path / 'users.json'
    users_file.write_text(json.dumps({'alice': {'sub_token': 'secret'}}), encoding='utf-8')

    class ServiceModule:
        USERS_FILE = Path(users_file)
        RULE_PACKS = {'easyconnect': {'label': 'EasyConnect', 'desc': 'VPN'}}
        RULE_PACK_ORDER = ('easyconnect',)
        TemplateConfigError = ValueError
        TemplateConflictError = RuntimeError
        state_store = SimpleNamespace(StateStoreError=RuntimeError)
        rules = ['MATCH,DIRECT']

        @staticmethod
        def request_multiplier_snapshot(function):
            return function

        @staticmethod
        def is_logged_in(_request):
            return True

        @staticmethod
        def load_json(_path, _default):
            return {'alice': {'sub_token': 'secret'}}

        @classmethod
        def load_template_rules_snapshot(cls):
            return list(cls.rules), 'b' * 64

        @staticmethod
        def validate_clash_rule(_rule):
            return True

        @classmethod
        def add_template_rule(cls, rule, expected_revision):
            assert expected_revision == 'b' * 64
            cls.rules.insert(0, rule)

    services = LegacyPanelServices(ServiceModule)
    payload = services.read_admin_rules(headers={}, path='/api/v1/admin/rules')
    assert payload['packs'] == [
        {'key': 'easyconnect', 'label': 'EasyConnect', 'description': 'VPN'}
    ]
    assert payload['users'] == ['alice']

    result = services.submit_rules_operation(
        headers={},
        path='/api/v1/admin/rules/add',
        form={
            'rule_type': ['DOMAIN'],
            'pattern': ['example.com'],
            'action': ['DIRECT'],
            'extra': [''],
            'template_revision': ['b' * 64],
        },
        client_address=('', 0),
        action='add',
    )
    assert result == {
        'ok': True,
        'action': 'add',
        'revision': 'b' * 64,
    }
    assert ServiceModule.rules[0] == 'DOMAIN,example.com,DIRECT'
