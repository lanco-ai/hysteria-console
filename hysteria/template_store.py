"""Subscription-template validation and locked storage transactions.

No HTTP or service globals: paths and side effects are supplied by the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager


def _load_yaml_file(path):
    import yaml

    text = path.read_text(encoding='utf-8')
    return yaml.safe_load(text) or {}


def _dump_yaml(data):
    import yaml

    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


class TemplateConfigError(ValueError):
    """The operator template exists but cannot be safely interpreted."""


class TemplateConflictError(RuntimeError):
    """The template changed after an operator opened an edit form."""


def validate_template_config(data):
    if not isinstance(data, dict):
        return False
    if any(key not in data for key in ('proxies', 'proxy-groups', 'rules')):
        return False
    proxies = data.get('proxies', [])
    groups = data.get('proxy-groups', [])
    rules = data.get('rules', [])
    if not all(isinstance(items, list) for items in (proxies, groups, rules)):
        return False
    proxy_names = set()
    for proxy in proxies:
        if not isinstance(proxy, dict):
            return False
        name = proxy.get('name')
        proxy_type = proxy.get('type')
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(proxy_type, str)
            or not proxy_type.strip()
            or name in proxy_names
        ):
            return False
        proxy_names.add(name)
    group_names = set()
    for group in groups:
        if not isinstance(group, dict):
            return False
        name = group.get('name')
        group_type = group.get('type')
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(group_type, str)
            or not group_type.strip()
            or name in group_names
        ):
            return False
        for member_key in ('proxies', 'use'):
            if member_key in group and (
                not isinstance(group[member_key], list)
                or any(
                    not isinstance(member, str) or not member.strip()
                    for member in group[member_key]
                )
            ):
                return False
        group_names.add(name)
    if any(not validate_clash_rule(rule) for rule in rules):
        return False
    try:
        import yaml

        round_trip = yaml.safe_load(_dump_yaml(data))
    except Exception:
        return False
    if not isinstance(round_trip, dict):
        return False
    return True


def validate_clash_rule(rule):
    if not isinstance(rule, str) or not rule or len(rule) > 2048:
        return False
    if any(ord(ch) < 32 for ch in rule):
        return False
    parts = [part.strip() for part in rule.split(',')]
    if len(parts) < 2 or not parts[0] or not parts[-1]:
        return False
    if parts[0] == 'MATCH':
        return len(parts) == 2
    return len(parts) >= 3 and bool(parts[1])


@dataclass(frozen=True)
class TemplateStore:
    path: Path
    lock: Callable[[], ContextManager[object]]
    write_atomic: Callable[[Path, str], object]
    apply_pack: Callable[[dict, str], bool]

    def _template_bytes_unlocked(self):
        if not self.path.exists():
            return b''
        return self.path.read_bytes()

    def _template_revision_unlocked(self):
        return hashlib.sha256(self._template_bytes_unlocked()).hexdigest()

    def _validate_template_revision_unlocked(self, expected_revision):
        expected = str(expected_revision or '').strip().lower()
        if not (
            re.fullmatch(r'[0-9a-f]{64}', expected)
            and hmac.compare_digest(self._template_revision_unlocked(), expected)
        ):
            raise TemplateConflictError('template revision changed')

    def load_template_config(self):
        """Load the subscription template as a dict. Returns {} if missing."""
        if not self.path.exists():
            return {}
        try:
            data = _load_yaml_file(self.path)
        except Exception as exc:
            raise TemplateConfigError('template YAML is invalid') from exc
        if not isinstance(data, dict):
            raise TemplateConfigError('template root must be a mapping')
        return data

    def load_template_config_snapshot(self):
        """Return a config and revision captured under the template lock."""
        with self.lock():
            raw = self._template_bytes_unlocked()
            try:
                import yaml

                data = yaml.safe_load(raw.decode('utf-8')) or {}
            except Exception as exc:
                raise TemplateConfigError('template YAML is invalid') from exc
            if not isinstance(data, dict):
                raise TemplateConfigError('template root must be a mapping')
            return data, hashlib.sha256(raw).hexdigest()

    def save_template_config(self, data):
        """Save dict to the subscription template."""
        self.write_atomic(self.path, _dump_yaml(data))

    def replace_template_config(self, data, expected_revision=None):
        with self.lock():
            if expected_revision is not None:
                self._validate_template_revision_unlocked(expected_revision)
            self.save_template_config(data)

    def load_template_rules(self):
        """Load rules list from the subscription template."""
        if not self.path.exists():
            return []
        data = self.load_template_config()
        rules = data.get('rules', [])
        if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
            raise TemplateConfigError('template rules must be a string list')
        return rules

    def load_template_rules_snapshot(self):
        data, revision = self.load_template_config_snapshot()
        rules = data.get('rules', [])
        if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
            raise TemplateConfigError('template rules must be a string list')
        return rules, revision

    def save_template_rules(self, rules):
        """Replace the rules section in the subscription template."""
        text = self.path.read_text(encoding='utf-8')
        lines = text.split('\n')
        start = None
        end = len(lines)
        for i, line in enumerate(lines):
            if start is None and re.match(r'^rules\s*:', line):
                start = i
            elif (
                start is not None
                and line
                and not line[0].isspace()
                and not line.startswith('#')
                and not re.match(r'^-(?:\s|$)', line)
            ):
                end = i
                break
        new_rule_lines = [
            '# 6. 规则',
            'rules:' if rules else 'rules: []',
        ]
        for r in rules:
            # JSON strings are valid YAML scalars and safely escape quotes,
            # backslashes and control characters without changing the rule value.
            new_rule_lines.append(f'  - {json.dumps(str(r), ensure_ascii=False)}')
        if start is None:
            result = lines + [''] + new_rule_lines
        else:
            cut = start - 1 if start > 0 and lines[start - 1].startswith('#') else start
            result = lines[:cut] + new_rule_lines + lines[end:]
        rendered = '\n'.join(result) + ('\n' if not result[-1].endswith('\n') else '')
        import yaml

        parsed = yaml.safe_load(rendered)
        if not isinstance(parsed, dict) or not isinstance(parsed.get('rules'), list):
            raise ValueError('rendered rules are not valid YAML')
        self.write_atomic(self.path, rendered)

    def add_template_rule(self, rule_str, expected_revision=None):
        with self.lock():
            if expected_revision is not None:
                self._validate_template_revision_unlocked(expected_revision)
            rules = self.load_template_rules()
            rules.insert(0, rule_str)
            self.save_template_rules(rules)

    def delete_template_rule(self, index, expected_revision=None, expected_rule=None):
        with self.lock():
            if expected_revision is not None:
                self._validate_template_revision_unlocked(expected_revision)
            rules = self.load_template_rules()
            if index < 0 or index >= len(rules):
                return False
            if expected_rule is not None and not hmac.compare_digest(
                rules[index].encode('utf-8'),
                str(expected_rule).encode('utf-8'),
            ):
                raise TemplateConflictError('rule changed at requested index')
            rules.pop(index)
            self.save_template_rules(rules)
            return True

    def replace_template_rules(self, rules, expected_revision=None):
        with self.lock():
            if expected_revision is not None:
                self._validate_template_revision_unlocked(expected_revision)
            self.save_template_rules(rules)

    def apply_rule_pack_to_template(self, pack_key, expected_revision=None):
        with self.lock():
            if expected_revision is not None:
                self._validate_template_revision_unlocked(expected_revision)
            data = self.load_template_config()
            if not self.apply_pack(data, pack_key):
                return False
            self.save_template_config(data)
            return True
