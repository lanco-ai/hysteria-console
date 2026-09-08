"""Health presentation handles probe errors without dropping card order."""

import importlib.util

import subscription_service as ss


def test_health_presentation_module_exists():
    assert importlib.util.find_spec('health_presentation') is not None


def test_online_summary_handles_unreadable_snapshot(monkeypatch):
    import health_presentation

    def fail(*args, **kwargs):
        raise OSError('unavailable')

    monkeypatch.setattr(ss, 'load_json', fail)
    presenter = ss._health_presentation()
    assert isinstance(presenter, health_presentation.HealthPresentation)
    assert presenter._probe_online_services() == {'ok': False, 'label': '未知'}
