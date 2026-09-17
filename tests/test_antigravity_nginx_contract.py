from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_antigravity_management_proxy_is_https_and_hides_public_health_inventory():
    config = (ROOT / 'nginx/antigravity-proxy-https.conf').read_text(encoding='utf-8')

    assert 'listen 9445 ssl;' in config
    assert 'proxy_pass http://127.0.0.1:8088;' in config
    assert 'location = /health {\n        return 404;\n    }' in config
    assert 'listen 8088' not in config
