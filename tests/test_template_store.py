"""Template transactions can run without the HTTP service."""

import importlib.util
from contextlib import contextmanager

import pytest


def test_template_store_is_independent():
    assert importlib.util.find_spec('template_store') is not None


def test_revision_conflict_preserves_newer_template(tmp_path):
    from template_store import TemplateConflictError, TemplateStore

    path = tmp_path / 'template.yaml'
    events = []

    @contextmanager
    def lock():
        events.append('enter')
        try:
            yield
        finally:
            events.append('exit')

    def write(target, text):
        target.write_text(text, encoding='utf-8')

    store = TemplateStore(path, lock, write, lambda data, key: False)
    store.replace_template_config({'rules': ['MATCH,DIRECT'], 'mixed-port': 7890})
    _, revision = store.load_template_config_snapshot()
    store.add_template_rule('DOMAIN,example.com,REJECT', revision)
    saved = path.read_bytes()
    with pytest.raises(TemplateConflictError):
        store.replace_template_rules([], revision)
    assert path.read_bytes() == saved
    assert store.load_template_rules() == ['DOMAIN,example.com,REJECT', 'MATCH,DIRECT']
    assert store.load_template_config()['mixed-port'] == 7890
    assert events == ['enter', 'exit'] * 4


def test_invalid_template_is_not_silently_replaced(tmp_path):
    from template_store import TemplateConfigError, TemplateStore

    path = tmp_path / 'template.yaml'
    path.write_text('- invalid-root\n', encoding='utf-8')

    @contextmanager
    def lock():
        yield

    store = TemplateStore(path, lock, None, None)
    with pytest.raises(TemplateConfigError):
        store.load_template_config_snapshot()
    assert path.read_text(encoding='utf-8') == '- invalid-root\n'
