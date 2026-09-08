import subscription_service as ss


def test_public_home_is_illustrative_and_does_not_read_private_state(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Public homepage must not load private state')
    monkeypatch.setattr(ss, 'load_json', forbidden)
    monkeypatch.setattr(ss, 'load_meta', forbidden)
    page = ss.render_home('private-host.example')
    assert '界面示意 · 非实时数据' in page
    assert 'private-host.example' not in page
    assert 'WireGuard' not in page
    assert 'alice' not in page
    assert page.count('data-demo=') == 3
    assert page.count('href="/login"') == 3
    assert 'id="services"' in page
    assert 'id="console-preview"' in page
    assert ss.HOME_JS_ETAG.strip('"') in page
    assert '{HOME_JS_ETAG' not in page


def test_home_previews_have_accessible_labels_and_no_js_fallback():
    page = ss.render_home('example')
    for key in ('traffic', 'users', 'health'):
        assert f'id="demo-tab-{key}"' in page
        assert f'id="demo-{key}" aria-labelledby="demo-tab-{key}"' in page
    assert 'class="site-demo-panel" hidden' not in page
    assert 'role="img" aria-label=' in page
