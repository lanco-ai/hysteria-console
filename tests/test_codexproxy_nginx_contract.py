from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_codexproxy_management_proxy_is_https_and_uses_loopback_backend():
    config = (ROOT / 'nginx/codexproxy-https.conf').read_text(encoding='utf-8')

    assert 'listen 9445 ssl;' in config
    assert 'return 302 /management.html;' in config
    assert 'proxy_pass http://127.0.0.1:8317;' in config
    assert 'proxy_buffering off;' in config
    assert 'listen 8317' not in config
