from contextlib import contextmanager

import subscription_service as ss


def _seed_template(path):
    path.write_text(
        "proxies: []\n"
        "# 6. 规则\n"
        "rules:\n"
        "  - 'DOMAIN,a.example,DIRECT'\n",
        encoding='utf-8',
    )


def test_template_rule_helpers_hold_template_lock(tmp_path, monkeypatch):
    template = tmp_path / 'template.yaml'
    _seed_template(template)
    monkeypatch.setattr(ss, 'TEMPLATE_FILE', template)

    events = []

    @contextmanager
    def fake_lock():
        events.append('enter')
        yield
        events.append('exit')

    monkeypatch.setattr(ss, 'template_lock', fake_lock)

    ss.add_template_rule('DOMAIN,b.example,REJECT')
    assert ss.delete_template_rule(1) is True
    ss.replace_template_rules(['DOMAIN,c.example,DIRECT'])

    assert events == ['enter', 'exit', 'enter', 'exit', 'enter', 'exit']
    assert ss.load_template_rules() == ['DOMAIN,c.example,DIRECT']


def test_delete_template_rule_rejects_out_of_range_under_lock(tmp_path, monkeypatch):
    template = tmp_path / 'template.yaml'
    _seed_template(template)
    lock = tmp_path / 'template.lock'
    monkeypatch.setattr(ss, 'TEMPLATE_FILE', template)
    monkeypatch.setattr(ss, 'TEMPLATE_LOCK_FILE', lock)

    assert ss.delete_template_rule(99) is False
    assert ss.load_template_rules() == ['DOMAIN,a.example,DIRECT']
    assert lock.exists()


def test_replace_template_config_holds_template_lock(tmp_path, monkeypatch):
    template = tmp_path / 'template.yaml'
    _seed_template(template)
    monkeypatch.setattr(ss, 'TEMPLATE_FILE', template)

    events = []

    @contextmanager
    def fake_lock():
        events.append('enter')
        yield
        events.append('exit')

    monkeypatch.setattr(ss, 'template_lock', fake_lock)

    ss.replace_template_config({'rules': ['DOMAIN,d.example,DIRECT']})

    assert events == ['enter', 'exit']
    assert ss.load_template_rules() == ['DOMAIN,d.example,DIRECT']
