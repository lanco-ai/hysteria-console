"""LancoAgent: natural-language rule changes for the administrator.

The network service owns the agent's rule service and every write to
``users.json``.  Turning the administrator's message into a typed intent needs
a language model whose settings live in the workbench + AI service, so the
completion is a loopback call to that service's ``/internal/v1/agent-intent``.
The returned intent is validated here again before any plan is built.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx

from .agent_rule_service import AgentRuleService, AgentServiceError

WORKBENCH_INTENT_URL = 'http://127.0.0.1:8085/internal/v1/agent-intent'
INTERNAL_TOKEN_FILE = Path('/etc/lancoai/internal-token')
INTERNAL_TOKEN_HEADER = 'X-Lancoai-Internal-Token'
MIN_TOKEN_LENGTH = 32
# The completion reads at most this many characters of each joined rule list.
CONTEXT_TEXT_LIMIT = 2800
# The model call itself may take up to 120 seconds.
INTENT_TIMEOUT_SECONDS = 150.0
MAX_INTENT_RESPONSE_BYTES = 64 * 1024
INTENT_ACTIONS = ('inspect', 'apply_pack', 'add_rule', 'delete_rule')
FORWARDED_ERRORS = frozenset({
    'settings_unavailable',
    'upstream_unavailable',
    'invalid_model_response',
    'unsupported_action',
    'invalid_rule_pack',
    'invalid_rule',
})


def _clip(items) -> list[str]:
    """Keep the prefix the completion would read: same text after its truncation."""
    kept: list[str] = []
    size = 0
    for item in items or ():
        if not isinstance(item, str):
            continue
        if size >= CONTEXT_TEXT_LIMIT:
            break
        kept.append(item[:CONTEXT_TEXT_LIMIT])
        size += len(item) + 1
    return kept


def _read_token(path: Path) -> str | None:
    try:
        token = Path(path).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return None
    return token if len(token) >= MIN_TOKEN_LENGTH else None


def _validated_intent(value) -> dict:
    if not isinstance(value, dict):
        raise AgentServiceError('invalid_model_response')
    action = str(value.get('action') or '').strip()
    if action not in INTENT_ACTIONS:
        raise AgentServiceError('unsupported_action')
    pack = str(value.get('pack') or '').strip()
    rule = str(value.get('rule') or '').strip()
    if action == 'apply_pack' and not pack:
        raise AgentServiceError('invalid_rule_pack')
    if action in ('add_rule', 'delete_rule') and not rule:
        raise AgentServiceError('invalid_rule')
    explanation = str(value.get('explanation') or '').strip()[:2000]
    return {'action': action, 'pack': pack, 'rule': rule, 'explanation': explanation}


class RemoteIntentCompleter:
    """Ask the workbench + AI service for a typed intent over loopback."""

    def __init__(
        self,
        url: str = WORKBENCH_INTENT_URL,
        *,
        token_file: Path | str = INTERNAL_TOKEN_FILE,
        timeout: float = INTENT_TIMEOUT_SECONDS,
        transport=None,
    ):
        self.url = url
        self.token_file = Path(token_file)
        self._client = httpx.Client(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def __call__(self, settings, *, message: str, target_user: str, context: dict) -> dict:
        del settings  # The model settings live in the workbench + AI service.
        token = _read_token(self.token_file)
        if token is None:
            raise AgentServiceError('settings_unavailable')
        body = json.dumps(
            {
                'message': message,
                'target_user': target_user,
                'context': {
                    'packs': [str(pack) for pack in context.get('packs', ())],
                    'rules': _clip(context.get('rules', ())),
                    'global_rules': _clip(context.get('global_rules', ())),
                },
            },
            ensure_ascii=False,
        ).encode('utf-8')
        try:
            response = self._client.post(
                self.url,
                content=body,
                headers={'Content-Type': 'application/json', INTERNAL_TOKEN_HEADER: token},
            )
        except httpx.HTTPError as exc:
            raise AgentServiceError('upstream_unavailable') from exc
        if len(response.content) > MAX_INTENT_RESPONSE_BYTES:
            raise AgentServiceError('invalid_model_response')
        try:
            payload = response.json()
        except ValueError as exc:
            raise AgentServiceError('upstream_unavailable') from exc
        if not isinstance(payload, dict):
            raise AgentServiceError('upstream_unavailable')
        if response.status_code == 200 and payload.get('ok') is True:
            return _validated_intent(payload.get('intent'))
        code = payload.get('error')
        if code in FORWARDED_ERRORS:
            raise AgentServiceError(code)
        # Token, routing or request problems mean the agent is not set up.
        if code in ('internal_token_unavailable', 'forbidden', 'not_found', 'bad_request'):
            raise AgentServiceError('settings_unavailable')
        raise AgentServiceError('upstream_unavailable')


_default_completer: RemoteIntentCompleter | None = None


def complete_agent_intent(settings, *, message: str, target_user: str, context: dict):
    """Module-level completion so callers and tests keep a stable seam."""
    global _default_completer
    if _default_completer is None:
        _default_completer = RemoteIntentCompleter()
    return _default_completer(
        settings, message=message, target_user=target_user, context=context
    )


class AgentOrchestrator:
    def __init__(self, service, *, completion: Callable = complete_agent_intent, rule_service=None):
        self.rule_service = rule_service or AgentRuleService(service)
        self.service = service
        self.completion = completion
        # Kept for the completion signature; the model settings live elsewhere.
        self.settings = None

    def plan(self, *, message: str, target_user: str, operator: str = 'admin', source_ip: str = ''):
        message = str(message or '').strip()
        target_user = str(target_user or '').strip()
        if not message or len(message) > 16 * 1024:
            raise AgentServiceError('invalid_message')
        if not target_user:
            raise AgentServiceError('target_required')
        current = self.rule_service.get_user_rules(target_user)
        packs = getattr(self.service, 'RULE_PACKS', {})
        intent = self.completion(
            self.settings,
            message=message,
            target_user=target_user,
            context={
                'packs': tuple(packs.keys()) if isinstance(packs, dict) else (),
                'rules': current['rules'],
                'global_rules': current.get('global_rules', ()),
                'merged_rules': current.get('merged_rules', ()),
            },
        )
        if intent['action'] == 'inspect':
            return {
                'ok': True,
                'action': 'inspect',
                'target_user': target_user,
                'explanation': intent.get('explanation') or '当前规则已读取。',
                'snapshot': current,
            }
        if intent['action'] == 'apply_pack':
            plan = self.rule_service.preview_rule_pack(
                target_user,
                intent.get('pack', ''),
                expected_revision=current['revision'],
                expected_global_revision=current.get('global_revision', ''),
                operator=operator,
                source_ip=source_ip,
            )
        else:
            plan = self.rule_service.preview_rule(
                target_user,
                'add' if intent['action'] == 'add_rule' else 'delete',
                intent.get('rule', ''),
                expected_revision=current['revision'],
                expected_global_revision=current.get('global_revision', ''),
                operator=operator,
                source_ip=source_ip,
            )
        return {
            'ok': True,
            'action': intent['action'],
            'explanation': intent.get('explanation') or '已生成规则变更预览。',
            'snapshot': current,
            'plan': plan,
        }
