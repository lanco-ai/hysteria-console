"""Static contracts for the opt-in React ASGI runtime entrypoint."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_react_server_uses_authoritative_service_and_build_assets():
    source = (ROOT / 'hysteria/react_server.py').read_text(encoding='utf-8')
    assert 'LegacyPanelServices(subscription_service)' in source
    assert 'react_dist=REACT_DIST' in source
    assert '_REACT_DIST_CANDIDATES' in source
    assert 'ThreadingHTTPServer' not in source


def test_react_systemd_unit_is_loopback_staged_and_single_worker():
    unit = (ROOT / 'systemd/hysteria-react.service').read_text(encoding='utf-8')
    assert '--host 127.0.0.1 --port 8083 --workers 1' in unit
    assert 'WorkingDirectory=/root/hysteria' in unit
    assert 'Requires=hysteria-subscription.service' in unit
    assert 'TasksMax=32' in unit
    assert 'deploy.sh' in unit
