"""Shared reset-log presentation data and legacy rendering contracts."""

import importlib
import json
from pathlib import Path

import pytest
import subscription_service as ss


def _read_reset_logs(*args, **kwargs):
    module = importlib.import_module('reset_log_data')
    return module.read_reset_logs(*args, **kwargs)


def _json_line(value):
    return json.dumps(value, ensure_ascii=False)


def test_read_reset_logs_returns_latest_valid_rows_as_unescaped_strings(tmp_path):
    path = tmp_path / 'usage_reset.log'
    path.write_text(
        '\n'.join(
            (
                _json_line(
                    {
                        'time': '2026-09-10 08:00:00',
                        'actor': 'first-admin',
                        'ip': '192.0.2.10',
                        'action': 'reset_usage_user',
                        'target': 'older-user',
                        'month': '2026-09',
                        'before': {'total': 1024},
                        'after': {'total': 2048},
                    }
                ),
                '',
                '{malformed',
                _json_line(['not', 'an', 'object']),
                _json_line(
                    {
                        'time': '<script>alert("time")</script>',
                        'actor': '<b>operator</b>',
                        'ip': '198.51.100.8',
                        'action': '<script>unknown()</script>',
                        'target': '<img src=x onerror=alert(1)>',
                        'month': '2026-10',
                        'before': {},
                        'after': {'total': 999},
                        'password_hash': 'must-not-escape-the-record',
                    }
                ),
            )
        )
        + '\n',
        encoding='utf-8',
    )

    result = _read_reset_logs(
        path,
        limit=300,
        action_label=lambda action: f'label:{action}',
        fmt_bytes=lambda value: f'{value} bytes',
    )

    assert result == {
        'limit': 300,
        'rows': [
            {
                'time': '<script>alert("time")</script>',
                'actor': '<b>operator</b>',
                'ip': '198.51.100.8',
                'action': 'label:<script>unknown()</script>',
                'target': '<img src=x onerror=alert(1)>',
                'month': '2026-10',
                'detail': '',
            },
            {
                'time': '2026-09-10 08:00:00',
                'actor': 'first-admin',
                'ip': '192.0.2.10',
                'action': 'label:reset_usage_user',
                'target': 'older-user',
                'month': '2026-09',
                'detail': '1024 bytes → 2048 bytes',
            },
        ],
    }


def test_read_reset_logs_limits_physical_lines_before_skipping_bad_records(tmp_path):
    path = tmp_path / 'usage_reset.log'
    older = _json_line({'time': 'outside-window', 'actor': 'old'})
    malformed_tail = ['{bad'] * 299
    newest = _json_line({'time': 'newest', 'actor': 'admin'})
    path.write_text('\n'.join((older, *malformed_tail, newest)) + '\n', encoding='utf-8')

    result = _read_reset_logs(
        path,
        action_label=lambda action: action,
        fmt_bytes=str,
    )

    assert result == {
        'limit': 300,
        'rows': [
            {
                'time': 'newest',
                'actor': 'admin',
                'ip': '',
                'action': '',
                'target': '',
                'month': '',
                'detail': '',
            }
        ],
    }


def test_read_reset_logs_returns_empty_rows_for_absent_file(tmp_path):
    result = _read_reset_logs(
        tmp_path / 'missing.log',
        limit=17,
        action_label=str,
        fmt_bytes=str,
    )

    assert result == {'limit': 17, 'rows': []}


@pytest.mark.parametrize('error_type', [PermissionError, OSError])
def test_read_reset_logs_propagates_io_failures(tmp_path, monkeypatch, error_type):
    path = tmp_path / 'usage_reset.log'

    def fail_open(self, *args, **kwargs):
        del self, args, kwargs
        raise error_type('private filesystem detail')

    monkeypatch.setattr(Path, 'open', fail_open)

    with pytest.raises(error_type, match='private filesystem detail'):
        _read_reset_logs(path, action_label=str, fmt_bytes=str)


def test_legacy_renderer_uses_shared_unescaped_rows_and_escapes_cells_once(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / 'usage_reset.log'
    path.write_text(
        _json_line(
            {
                'time': '2026-09-12 10:11:12',
                'actor': '<b>operator</b>',
                'ip': '203.0.113.4',
                'action': 'reset_usage_user',
                'target': 'alice & bob',
                'month': '2026-09',
                'before': {'total': 1024},
                'after': {'total': 2048},
            }
        )
        + '\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(ss, 'RESET_LOG_FILE', path)

    rendered = ss.render_reset_logs('panel.example')

    assert (
        '<tr><td class="small">2026-09-12 10:11:12</td>'
        '<td>&lt;b&gt;operator&lt;/b&gt;</td><td class="small">203.0.113.4</td>'
        '<td>清除用户流量</td><td>alice &amp; bob</td>'
        '<td class="small">2026-09</td>'
        '<td class="small">1.00 KB → 2.00 KB</td></tr>'
    ) in rendered
    assert '&amp;lt;b&amp;gt;' not in rendered
