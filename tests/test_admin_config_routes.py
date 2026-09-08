"""Configuration POST contracts against real isolated template storage."""

import importlib
import json
from urllib.parse import urlencode

import pytest
import subscription_service as ss
import yaml

from tests.test_reliability_regressions import _configure_state, _request, _running_server

CASES = [
    (
        '/admin/config/save',
        {'config_json': '{"proxies": [], "proxy-groups": [], "rules": ["MATCH,REJECT"]}'},
        ['MATCH,REJECT'],
    ),
    (
        '/admin/rules/add',
        {'pattern': 'example.com'},
        ['DOMAIN-SUFFIX,example.com,DIRECT', 'MATCH,DIRECT'],
    ),
    ('/admin/rules/delete', {'index': '0', 'expected_rule': 'MATCH,DIRECT'}, []),
    (
        '/admin/rules/raw',
        {'rules_raw': 'DOMAIN,example.com,DIRECT\nMATCH,REJECT'},
        ['DOMAIN,example.com,DIRECT', 'MATCH,REJECT'],
    ),
]


@pytest.fixture
def template_state(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch)
    path = tmp_path / 'template.yaml'
    path.write_text('rules:\n  - MATCH,DIRECT\n', encoding='utf-8')
    monkeypatch.setattr(ss, 'TEMPLATE_FILE', path)
    monkeypatch.setattr(ss, 'TEMPLATE_LOCK_FILE', tmp_path / 'template.lock')
    return path, {'Cookie': f'sid={ss.create_session()}', 'Origin': 'http://panel.test'}


@pytest.mark.parametrize('path,form,rules', CASES)
@pytest.mark.parametrize('mode', ['success', 'conflict', 'anonymous', 'cross-origin'])
def test_template_post_contract(template_state, path, form, rules, mode):
    template, headers = template_state
    before = template.read_bytes()
    _, revision = ss.load_template_config_snapshot()
    form = dict(form, template_revision='stale' if mode == 'conflict' else revision)
    if mode == 'anonymous':
        headers.pop('Cookie')
    if mode == 'cross-origin':
        headers['Origin'] = 'https://attacker.invalid'
    with _running_server() as server:
        response = _request(server, 'POST', path, body=urlencode(form), headers=headers)
    assert (
        response.status
        == {'success': 302, 'conflict': 409, 'anonymous': 302, 'cross-origin': 403}[mode]
    )
    if mode == 'success':
        assert yaml.safe_load(template.read_text())['rules'] == rules
        assert response.headers['location'].startswith('/admin/')
    else:
        assert template.read_bytes() == before
        if mode == 'anonymous':
            assert response.headers['location'] == '/login'


@pytest.mark.parametrize(
    'path,form,status',
    [
        ('/admin/config/save', {'config_json': ''}, 422),
        ('/admin/config/save', {'config_json': '{'}, 422),
        ('/admin/config/save', {'config_json': json.dumps({'proxies': {}})}, 422),
        ('/admin/rules/add', {'pattern': 'bad,pattern'}, 302),
        ('/admin/rules/delete', {'index': 'invalid'}, 302),
        ('/admin/rules/raw', {'rules_raw': 'invalid'}, 422),
    ],
)
def test_invalid_template_post_does_not_write(template_state, path, form, status):
    template, headers = template_state
    before = template.read_bytes()
    with _running_server() as server:
        response = _request(server, 'POST', path, body=urlencode(form), headers=headers)
    assert response.status == status
    assert template.read_bytes() == before
    if status == 302:
        assert 'msg=err:' in response.headers['location']


def test_unknown_config_route_has_no_side_effects():
    module = importlib.import_module('admin_config_routes')
    assert module.handle_write(object(), object(), path='/admin/rule-pack/apply', form={}) is False
