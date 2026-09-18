import json
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


def test_complete_agent_intent_reads_chat_settings_store_before_forwarding(tmp_path, monkeypatch):
    from web_api import agent_service
    from web_api.chat_service import ChatSettingsStore

    settings_path = Path(tmp_path) / 'chat-settings.json'
    settings_path.write_text(json.dumps({
        'base_url': 'https://chat.example.test/v1',
        'api_key': 'test-key',
        'temperature': 0.2,
    }), encoding='utf-8')
    seen = {}

    def fake_forward(settings, messages, **kwargs):
        seen['settings'] = settings
        seen['messages'] = messages
        seen['kwargs'] = kwargs
        assert settings.api_key == 'test-key'
        return {'choices': [{'message': {'content': '{"action":"inspect","explanation":"已读取"}'}}]}

    monkeypatch.setattr(agent_service, 'forward_chat', fake_forward)
    result = agent_service.complete_agent_intent(
        ChatSettingsStore(settings_path),
        message='查看当前用户规则',
        target_user='alice',
        context={'packs': (), 'rules': []},
    )
    assert result['action'] == 'inspect'
    assert seen['kwargs']['model'] == 'gemini-3.8-flash-high'


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
