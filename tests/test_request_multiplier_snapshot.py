import subscription_service as ss
import pytest
import user_compat
import usage_dashboard


@pytest.mark.parametrize('method,target', [('do_GET','handle_get'),('do_HEAD','handle_get'),('do_POST','_do_POST')])
def test_get_uses_one_multiplier_and_next_request_reads_fresh(monkeypatch, method, target):
    values = iter([2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr(ss.display_config, 'effective_display_multiplier_strict', lambda **kw: next(values))
    observed = []
    handler = object.__new__(ss.Handler)
    setattr(handler, target, lambda **kw: observed.append((ss.current_display_multiplier(), ss.current_display_multiplier())))
    getattr(handler, method)()
    getattr(handler, method)()
    assert observed == [(2.0, 2.0), (3.0, 3.0)]


@pytest.mark.parametrize('cfg,expected', [({},2),({'max_devices':0},0),({'max_devices':'3'},3),({'max_devices':True},2),({'max_devices':-1},2),({'max_devices':'bad'},2), (None,2)])
def test_device_limits_preserve_contract(cfg, expected):
    for parse in (ss.configured_max_devices, usage_dashboard.configured_max_devices, user_compat.configured_max_devices):
        assert parse(cfg) == expected


def test_snapshot_resets_after_failure(monkeypatch):
    values = iter([2.0, 3.0])
    monkeypatch.setattr(ss.display_config, 'effective_display_multiplier_strict', lambda **kw: next(values))
    @ss.request_multiplier_snapshot
    def fail():
        assert ss.current_display_multiplier() == 2.0
        raise RuntimeError('test')
    with pytest.raises(RuntimeError):
        fail()
    assert ss.current_display_multiplier() == 3.0
