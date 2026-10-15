import json

import pytest
from pathlib import Path

from fastapi.testclient import TestClient

from web_api import create_app
from web_api.agent_rule_service import AgentRuleService, AgentServiceError


class _SessionServices:
    def __init__(self, state):
        self.service_module = state

    def read_session(self, *, headers, path):
        del path
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        from web_api.services import LoginRequired
        raise LoginRequired


class _State:
    RULE_PACKS = {
        'overleaf': {
            'label': 'Overleaf 加速',
            'desc': 'Overleaf 代理',
            'rules': ['DOMAIN-SUFFIX,overleaf.com,🚀 节点选择'],
        },
    }
    RULE_PACK_ORDER = ('overleaf',)

    def __init__(self, tmp_path):
        self.USERS_FILE = Path(tmp_path) / 'users.json'
        self.USERS_FILE.write_text(
            json.dumps({'alice': {'clash_rules': ['MATCH,DIRECT']}}),
            encoding='utf-8',
        )

    @staticmethod
    def load_json(path, default):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default

    @staticmethod
    def save_json(path, data):
        Path(path).write_text(json.dumps(data), encoding='utf-8')

    @staticmethod
    def user_config_revision(cfg):
        import hashlib
        return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_agent_rule_service_preview_apply_and_undo(tmp_path):
    state = _State(tmp_path)
    service = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    snapshot = service.get_user_rules('alice')
    plan = service.preview_rule_pack('alice', 'overleaf', expected_revision=snapshot['revision'])
    assert plan['additions'] == [
        'DOMAIN-SUFFIX,overleaf.com,🚀 节点选择',
        'DOMAIN-SUFFIX,overleafusercontent.com,🚀 节点选择',
        'DOMAIN-SUFFIX,sharelatex.com,🚀 节点选择',
    ]
    applied = service.apply_change(plan['change_id'])
    assert applied['ok'] is True
    assert service.get_user_rules('alice')['rules'][0].startswith('DOMAIN-SUFFIX,overleaf.com')
    undone = service.undo_change(plan['change_id'])
    assert undone['ok'] is True
    assert service.get_user_rules('alice')['rules'] == ['MATCH,DIRECT']


def test_agent_rule_service_rejects_stale_revision(tmp_path):
    state = _State(tmp_path)
    service = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    try:
        service.preview_rule_pack('alice', 'overleaf', expected_revision='0' * 64)
    except AgentServiceError as exc:
        assert exc.code == 'revision_conflict'
    else:
        raise AssertionError('stale revision must be rejected')


def test_agent_rule_service_supports_scoped_custom_add_and_delete(tmp_path):
    state = _State(tmp_path)
    service = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    add = service.preview_rule('alice', 'add', 'DOMAIN,internal.example,DIRECT')
    assert add['operation'] == 'add'
    assert add['additions'] == ['DOMAIN,internal.example,DIRECT']
    applied = service.apply_change(add['change_id'])
    assert applied['snapshot']['rules'][0] == 'DOMAIN,internal.example,DIRECT'
    assert applied['snapshot']['merged_rules'][0] == 'DOMAIN,internal.example,DIRECT'
    assert 'DOMAIN,internal.example,DIRECT' in service.get_user_rules('alice')['rules']
    delete = service.preview_rule('alice', 'delete', 'DOMAIN,internal.example,DIRECT')
    assert delete['removals'] == ['DOMAIN,internal.example,DIRECT']
    service.apply_change(delete['change_id'])
    assert 'DOMAIN,internal.example,DIRECT' not in service.get_user_rules('alice')['rules']


def test_agent_rule_service_does_not_delete_inherited_global_rule(tmp_path):
    state = _State(tmp_path)
    state.load_template_rules_snapshot = lambda: (
        ['DOMAIN-SUFFIX,global.example,DIRECT'],
        'global-revision-1',
    )
    service = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    try:
        service.preview_rule('alice', 'delete', 'DOMAIN-SUFFIX,global.example,DIRECT')
    except AgentServiceError as exc:
        assert exc.code == 'global_rule_inherited'
    else:
        raise AssertionError('inherited global rule must not be deleted as a user override')


def test_agent_snapshot_includes_user_global_and_merged_rules(tmp_path):
    state = _State(tmp_path)
    state.load_template_rules_snapshot = lambda: (
        ['DOMAIN-SUFFIX,global.example,DIRECT', 'MATCH,🚀 节点选择'],
        'global-revision-1',
    )
    service = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    snapshot = service.get_user_rules('alice')
    assert snapshot['rules'] == ['MATCH,DIRECT']
    assert snapshot['global_rules'] == [
        'DOMAIN-SUFFIX,global.example,DIRECT',
        'MATCH,🚀 节点选择',
    ]
    assert snapshot['global_revision'] == 'global-revision-1'
    assert snapshot['merged_rules'] == [
        'MATCH,DIRECT',
        'DOMAIN-SUFFIX,global.example,DIRECT',
        'MATCH,🚀 节点选择',
    ]


def test_agent_plan_rejects_changed_global_template_revision(tmp_path):
    from web_api.agent_service import AgentOrchestrator

    state = _State(tmp_path)
    state.load_template_rules_snapshot = lambda: (['MATCH,DIRECT'], 'global-revision-1')
    rule_service = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    agent = AgentOrchestrator(
        state,
        rule_service=rule_service,
        completion=lambda *_args, **_kwargs: {
            'action': 'add_rule',
            'rule': 'DOMAIN,internal.example,DIRECT',
            'pack': '',
            'explanation': '新增规则',
        },
    )
    plan = agent.plan(message='增加规则', target_user='alice')
    state.load_template_rules_snapshot = lambda: (['MATCH,DIRECT'], 'global-revision-2')
    try:
        rule_service.apply_change(plan['plan']['change_id'])
    except AgentServiceError as exc:
        assert exc.code == 'revision_conflict'
    else:
        raise AssertionError('changed global template must invalidate pending plan')


def test_agent_orchestrator_routes_custom_rule_intent_to_preview(tmp_path):
    from web_api.agent_service import AgentOrchestrator

    state = _State(tmp_path)
    rule_service = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    agent = AgentOrchestrator(
        state,
        rule_service=rule_service,
        completion=lambda *_args, **_kwargs: {
            'action': 'add_rule',
            'rule': 'PROCESS-NAME,Example.exe,DIRECT',
            'pack': '',
            'explanation': '为该用户增加进程直连规则',
        },
    )
    result = agent.plan(message='让 Example.exe 直连', target_user='alice')
    assert result['action'] == 'add_rule'
    assert result['plan']['additions'] == ['PROCESS-NAME,Example.exe,DIRECT']


def _remote(handler, tmp_path, token='t' * 48):
    import httpx
    from web_api.agent_service import RemoteIntentCompleter

    token_file = Path(tmp_path) / 'internal-token'
    if token is not None:
        token_file.write_text(token + '\n', encoding='utf-8')
    return RemoteIntentCompleter(
        'http://workbench.test/internal/v1/agent-intent',
        token_file=token_file,
        transport=httpx.MockTransport(handler),
    )


def test_remote_intent_sends_what_the_completion_reads_and_validates_the_answer(tmp_path):
    import httpx
    from web_api.agent_service import CONTEXT_TEXT_LIMIT

    seen = {}

    def handler(request):
        seen['token'] = request.headers['x-lancoai-internal-token']
        seen['body'] = json.loads(request.content)
        return httpx.Response(
            200, json={'ok': True, 'intent': {'action': 'add_rule', 'rule': ' DOMAIN,a.test,DIRECT '}}
        )

    rules = [f'DOMAIN,host-{index}.example,DIRECT' for index in range(400)]
    completer = _remote(handler, tmp_path)
    intent = completer(
        None,
        message='让 alice 直连 a.test',
        target_user='alice',
        context={'packs': ('openai',), 'rules': rules, 'global_rules': [], 'merged_rules': rules},
    )
    assert intent == {'action': 'add_rule', 'pack': '', 'rule': 'DOMAIN,a.test,DIRECT', 'explanation': ''}
    assert seen['token'] == 't' * 48
    body = seen['body']
    assert body['message'] == '让 alice 直连 a.test' and body['target_user'] == 'alice'
    assert body['context']['packs'] == ['openai'] and 'merged_rules' not in body['context']
    sent = body['context']['rules']
    assert len(sent) < len(rules)
    # Same model input: the completion joins each list and keeps 2800 characters.
    assert '\n'.join(sent)[:CONTEXT_TEXT_LIMIT] == '\n'.join(rules)[:CONTEXT_TEXT_LIMIT]


@pytest.mark.parametrize('token', [None, 'short'])
def test_remote_intent_fails_closed_without_a_usable_token(tmp_path, token):
    def handler(request):
        raise AssertionError('no request without a token')

    with pytest.raises(AgentServiceError) as error:
        _remote(handler, tmp_path, token=token)(None, message='m', target_user='alice', context={})
    assert error.value.code == 'settings_unavailable'


@pytest.mark.parametrize(
    'response, code',
    [
        ((503, {'ok': False, 'error': 'settings_unavailable'}), 'settings_unavailable'),
        ((502, {'ok': False, 'error': 'upstream_unavailable'}), 'upstream_unavailable'),
        ((502, {'ok': False, 'error': 'invalid_model_response'}), 'invalid_model_response'),
        ((422, {'ok': False, 'error': 'invalid_rule_pack'}), 'invalid_rule_pack'),
        ((503, {'ok': False, 'error': 'internal_token_unavailable'}), 'settings_unavailable'),
        ((403, {'ok': False, 'error': 'forbidden'}), 'settings_unavailable'),
        ((404, {'error': 'not_found'}), 'settings_unavailable'),
        ((500, {'error': 'internal_error'}), 'upstream_unavailable'),
        ((200, {'ok': True, 'intent': {'action': 'drop_table'}}), 'unsupported_action'),
        ((200, {'ok': True, 'intent': {'action': 'apply_pack'}}), 'invalid_rule_pack'),
        ((200, {'ok': True, 'intent': {'action': 'delete_rule', 'rule': ' '}}), 'invalid_rule'),
        ((200, {'ok': True, 'intent': 'inspect'}), 'invalid_model_response'),
        ((200, ['not', 'an', 'object']), 'upstream_unavailable'),
    ],
)
def test_remote_intent_maps_every_failure_to_an_agent_code(tmp_path, response, code):
    import httpx

    status, payload = response
    completer = _remote(lambda request: httpx.Response(status, json=payload), tmp_path)
    with pytest.raises(AgentServiceError) as error:
        completer(None, message='m', target_user='alice', context={})
    assert error.value.code == code


def test_remote_intent_maps_transport_and_framing_failures(tmp_path):
    import httpx

    def refused(request):
        raise httpx.ConnectError('refused', request=request)

    for handler, code in (
        (refused, 'upstream_unavailable'),
        (lambda request: httpx.Response(200, content=b'not json'), 'upstream_unavailable'),
        (lambda request: httpx.Response(200, content=b'x' * (64 * 1024 + 1)), 'invalid_model_response'),
    ):
        with pytest.raises(AgentServiceError) as error:
            _remote(handler, tmp_path)(None, message='m', target_user='alice', context={})
        assert error.value.code == code


def test_agent_apply_recovers_when_audit_finalize_fails(tmp_path):
    state = _State(tmp_path)

    class FlakyAudit(AgentRuleService):
        saves = 0

        def _save_changes(self, data):
            self.saves += 1
            if self.saves == 3:
                raise AgentServiceError('state_unavailable')
            return super()._save_changes(data)

    service = FlakyAudit(state, changes_path=Path(tmp_path) / 'changes.json')
    plan = service.preview_rule_pack('alice', 'overleaf')
    try:
        service.apply_change(plan['change_id'])
    except AgentServiceError as exc:
        assert exc.code == 'state_unavailable'
    else:
        raise AssertionError('final audit failure should be surfaced')
    recovered = AgentRuleService(state, changes_path=Path(tmp_path) / 'changes.json')
    result = recovered.apply_change(plan['change_id'])
    assert result['ok'] is True


def test_agent_routes_require_admin_and_expose_model_and_apply_contract(tmp_path, monkeypatch):
    state = _State(tmp_path)
    services = _SessionServices(state)
    from web_api import agent_routes

    monkeypatch.setattr(
        agent_routes,
        'complete_agent_intent',
        lambda *_args, **_kwargs: {
            'action': 'apply_pack',
            'pack': 'overleaf',
            'explanation': '为 Overleaf 启用代理',
        },
    )
    with TestClient(create_app(services)) as client:
        denied = client.post(
            '/api/v1/admin/agent/plan',
            headers={'Origin': 'http://testserver', 'Content-Type': 'application/json'},
            json={'message': '启用 Overleaf', 'target_user': 'alice'},
        )
        assert denied.status_code == 401
        planned = client.post(
            '/api/v1/admin/agent/plan',
            headers={'Origin': 'http://testserver', 'Cookie': 'sid=admin'},
            json={'message': '启用 Overleaf', 'target_user': 'alice'},
        )
        assert planned.status_code == 200
        payload = planned.json()
        assert payload['plan']['target_user'] == 'alice'
        assert payload['plan']['requires_confirmation'] is True
        applied = client.post(
            '/api/v1/admin/agent/apply',
            headers={'Origin': 'http://testserver', 'Cookie': 'sid=admin'},
            json={'change_id': payload['plan']['change_id']},
        )
        assert applied.status_code == 200
        assert applied.json()['result']['ok'] is True
