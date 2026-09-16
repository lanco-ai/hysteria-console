"""Static deployment boundaries for the unified FastAPI backend."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nginx_react_templates_use_only_fastapi_port():
    for name in ('hysteria-panel-react.conf', 'hysteria-panel-react-https.conf'):
        text = (ROOT / 'nginx' / name).read_text(encoding='utf-8')
        assert 'proxy_pass http://127.0.0.1:8081' not in text
        assert 'proxy_pass http://127.0.0.1:8082' not in text
        assert 'proxy_pass http://127.0.0.1:8083' in text


def test_hysteria_auth_and_units_use_the_unified_service():
    config = (ROOT / 'hysteria/config.yaml.tpl').read_text(encoding='utf-8')
    assert 'http://127.0.0.1:8083/auth' in config
    react = (ROOT / 'systemd/hysteria-react.service').read_text(encoding='utf-8')
    assert 'Requires=hysteria-subscription.service' not in react
    server = (ROOT / 'systemd/hysteria-server.service').read_text(encoding='utf-8')
    assert 'hysteria-auth.service' not in server
    assert 'http://127.0.0.1:8083/readyz' in server


def test_legacy_http_servers_are_not_production_entrypoints():
    react_server = (ROOT / 'hysteria/react_server.py').read_text(encoding='utf-8')
    assert 'subscription_service' in react_server
    assert 'uvicorn' not in react_server
    assert 'BoundedThreadingHTTPServer(LISTEN' not in react_server
