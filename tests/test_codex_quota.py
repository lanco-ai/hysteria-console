import json
from pathlib import Path

import pytest

import codex_dashboard
import codex_quota


def raw_limits(*, five_used=25, week_used=40):
    return {
        'rateLimits': {
            'limitId': 'codex',
            'planType': 'pro',
            'primary': {
                'usedPercent': five_used,
                'windowDurationMins': 300,
                'resetsAt': 1_800_001_000,
            },
            'secondary': {
                'usedPercent': week_used,
                'windowDurationMins': 10080,
                'resetsAt': 1_800_500_000,
            },
            'credits': {'hasCredits': False, 'unlimited': False, 'balance': '0'},
        },
        'rateLimitsByLimitId': {
            'codex': {
                'limitId': 'codex',
                'planType': 'pro',
                'primary': {
                    'usedPercent': five_used,
                    'windowDurationMins': 300,
                    'resetsAt': 1_800_001_000,
                },
                'secondary': {
                    'usedPercent': week_used,
                    'windowDurationMins': 10080,
                    'resetsAt': 1_800_500_000,
                },
                'credits': {'hasCredits': False, 'unlimited': False, 'balance': '0'},
            },
        },
        'rateLimitResetCredits': {'availableCount': 2, 'credits': None},
    }


def test_normalize_identifies_windows_by_duration_and_whitelists_fields():
    result = raw_limits(five_used=84, week_used=15)
    result['secret'] = 'must-not-survive'

    latest = codex_quota.normalize_rate_limits(result, captured_at=1_800_000_000)

    assert latest['five_hour']['remaining_percent'] == 16
    assert latest['weekly']['remaining_percent'] == 85
    assert latest['five_hour']['window_minutes'] == 300
    assert latest['weekly']['window_minutes'] == 10080
    assert latest['reset_credits_available'] == 2
    assert latest['available_limit_ids'] == ['codex']
    assert 'secret' not in json.dumps(latest)


def test_normalize_handles_current_week_only_response_without_inventing_five_hour():
    result = raw_limits()
    snapshot = result['rateLimitsByLimitId']['codex']
    snapshot['primary'] = {
        'usedPercent': 0,
        'windowDurationMins': 10080,
        'resetsAt': 1_800_500_000,
    }
    snapshot['secondary'] = None

    latest = codex_quota.normalize_rate_limits(result, captured_at=1_800_000_000)

    assert latest['five_hour'] is None
    assert latest['weekly']['remaining_percent'] == 100


def test_query_rate_limits_performs_initialize_handshake(tmp_path):
    fake = tmp_path / 'fake-codex'
    fake.write_text(
        '''#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 1:
        print(json.dumps({"id": 1, "result": {"codexHome": "/tmp", "platformFamily": "unix", "platformOs": "linux", "userAgent": "test"}}), flush=True)
    elif message.get("id") == 2:
        print(json.dumps({"id": 2, "result": {"rateLimits": {"limitId": "codex", "planType": "pro", "primary": {"usedPercent": 7, "windowDurationMins": 300, "resetsAt": 1800001000}, "secondary": {"usedPercent": 9, "windowDurationMins": 10080, "resetsAt": 1800500000}}}}), flush=True)
''',
        encoding='utf-8',
    )
    fake.chmod(0o755)

    result = codex_quota.query_rate_limits(codex_bin=fake, timeout=5)

    assert result['rateLimits']['primary']['usedPercent'] == 7
    assert result['rateLimits']['secondary']['windowDurationMins'] == 10080


def test_compaction_keeps_recent_detail_and_bounds_older_history():
    now = 1_800_000_000
    samples = []
    for ts in range(now - 40 * 86400, now + 1, 180):
        samples.append({
            'ts': ts,
            'five_hour_remaining': ts % 101,
            'weekly_remaining': (ts // 2) % 101,
            'five_hour_resets_at': now + 300,
            'weekly_resets_at': now + 10080,
        })

    compacted = codex_quota.compact_samples(samples, now=now)

    assert compacted[-1]['ts'] == now
    assert len(compacted) < 4_100
    recent = [row for row in compacted if row['ts'] >= now - 86400]
    assert len(recent) >= 475
    old = [row for row in compacted if row['ts'] < now - 31 * 86400]
    assert len(old) <= 110


def test_collect_once_persists_success_and_preserves_it_on_failure(tmp_path):
    state_file = tmp_path / 'quota.json'
    now = 1_800_000_000

    state = codex_quota.collect_once(
        state_file=state_file,
        query_fn=lambda: raw_limits(five_used=10, week_used=20),
        now=now,
    )
    assert state['last_success_at'] == now
    assert state['latest']['five_hour']['remaining_percent'] == 90
    assert len(state['samples']) == 1

    def fail():
        raise codex_quota.CodexQuotaError('temporary test failure')

    with pytest.raises(codex_quota.CodexQuotaError):
        codex_quota.collect_once(
            state_file=state_file,
            query_fn=fail,
            now=now + 180,
        )

    saved = json.loads(state_file.read_text(encoding='utf-8'))
    assert saved['last_success_at'] == now
    assert saved['last_attempt_at'] == now + 180
    assert saved['consecutive_failures'] == 1
    assert saved['last_error'] == 'temporary test failure'
    assert len(saved['samples']) == 1


def test_legacy_csv_migration_distinguishes_two_window_and_week_only_rows(tmp_path):
    csv_file = tmp_path / 'legacy.csv'
    state_file = tmp_path / 'quota.json'
    csv_file.write_text(
        'timestamp,plan,session_used,session_remaining,session_reset,weekly_used,weekly_remaining,weekly_reset,status,error\n'
        '2026-07-12T18:03:42+00:00,pro,4,96,2026-07-12T22:30:48+00:00,76,24,2026-07-18T06:07:17+00:00,success,\n'
        '2026-07-12T19:02:11+00:00,pro,0,100,2026-07-19T18:58:17+00:00,,,,success,\n',
        encoding='utf-8',
    )
    now = 1_783_886_400  # 2026-07-12 20:00 UTC, inside retention.

    imported = codex_quota.import_legacy_csv(
        csv_file=csv_file, state_file=state_file, now=now,
    )

    state = json.loads(state_file.read_text(encoding='utf-8'))
    assert imported == 2
    assert state['samples'][0]['five_hour_remaining'] == 96
    assert state['samples'][0]['weekly_remaining'] == 24
    assert state['samples'][1]['five_hour_remaining'] is None
    assert state['samples'][1]['weekly_remaining'] == 100
    assert state['legacy_source_records'] == 2


def test_dashboard_payload_aggregates_ranges_and_reports_freshness(tmp_path):
    state_file = tmp_path / 'quota.json'
    now = 1_800_000_000
    latest = codex_quota.normalize_rate_limits(raw_limits(), captured_at=now)
    samples = []
    for ts in range(now - 7 * 86400, now + 1, 180):
        samples.append({
            'ts': ts,
            'five_hour_remaining': 75,
            'weekly_remaining': 60,
            'five_hour_resets_at': now + 1000,
            'weekly_resets_at': now + 500000,
        })
    state_file.write_text(json.dumps({
        'version': 1,
        'last_attempt_at': now,
        'last_success_at': now,
        'latest': latest,
        'samples': samples,
    }), encoding='utf-8')

    day = codex_quota.build_dashboard_payload(
        state_file=state_file, range_key='day', now=now,
    )
    week = codex_quota.build_dashboard_payload(
        state_file=state_file, range_key='week', now=now,
    )

    assert day['freshness']['status'] == 'live'
    assert day['windows']['five_hour']['remaining_percent'] == 75
    assert 475 <= len(day['points']) <= 481
    assert len(week['points']) <= 337
    assert week['history']['bucket_seconds'] == 1800
    assert week['poll_interval_seconds'] == 180


def test_dashboard_page_contains_interactive_chart_and_missing_window_copy():
    payload = {
        'account': {'plan_type': 'pro', 'limit_id': 'codex', 'reset_credits_available': 2},
        'freshness': {'status': 'live', 'last_success_at': 1_800_000_000},
        'windows': {
            'five_hour': {'label': '5 小时额度', 'available': False},
            'weekly': {
                'label': '周额度', 'available': True,
                'used_percent': 10, 'remaining_percent': 90,
                'resets_at': 1_800_500_000,
            },
        },
    }
    captured = {}

    def shell(active, title, content, **kwargs):
        captured.update(active=active, title=title, kwargs=kwargs)
        return content

    page = codex_dashboard.render_page(
        payload, render_admin_shell=shell, asset_version='abc123',
    )

    assert captured['active'] == 'codex'
    assert 'data-range="day"' in page
    assert 'data-range="year"' in page
    assert 'id="codex-quota-chart"' in page
    assert 'class="codex-dashboard is-week-only"' in page
    assert 'viewBox="0 0 1200 500"' in page
    assert '周额度余量与变化时刻' in page
    assert 'data-series="five-hour"' in page
    assert 'data-col="five-hour"' in page
    assert 'data-role="records-body"' in page
    assert '最近采集明细' in page
    assert '当前账户响应中未提供这个额度窗口' in page
    assert '<script src="/static/codex-quota.js?v=abc123" defer></script>' in page

    payload['windows']['five_hour'] = {
        'label': '5 小时额度', 'available': True,
        'used_percent': 20, 'remaining_percent': 80,
        'resets_at': 1_800_001_000,
    }
    page_with_both = codex_dashboard.render_page(
        payload, render_admin_shell=shell, asset_version='abc123',
    )
    assert 'class="codex-dashboard is-week-only"' not in page_with_both


def test_codex_routes_static_asset_and_nav_are_wired():
    service = Path('hysteria/subscription_service.py').read_text(encoding='utf-8')

    assert "('codex', '/admin/codex', 'Codex 额度', 'chart')" in service
    assert "if path == '/admin/codex':" in service
    assert "if path == '/admin/codex.json':" in service
    assert "if path == '/static/codex-quota.js':" in service


def test_timer_and_deploy_use_three_minute_one_shot_collector():
    timer = Path('systemd/codex-quota-collector.timer').read_text(encoding='utf-8')
    service = Path('systemd/codex-quota-collector.service').read_text(encoding='utf-8')
    deploy = Path('deploy.sh').read_text(encoding='utf-8')

    assert 'OnUnitActiveSec=3min' in timer
    assert 'Persistent=true' in timer
    assert 'Type=oneshot' in service
    assert '/root/hysteria/codex_quota.py collect' in service
    assert 'MemoryMax=256M' in service
    assert 'codex-quota-collector.timer' in deploy
    assert 'hysteria/codex_quota.py' in deploy
    assert 'hysteria/codex_quota.js' in deploy
    assert 'migrate-legacy' in deploy


def test_codex_frontend_is_framework_free_and_pauses_background_fetches():
    script = Path('hysteria/codex_quota.js').read_text(encoding='utf-8')
    styles = Path('hysteria/admin.css').read_text(encoding='utf-8')

    assert 'RANGE_SECONDS = {day:' in script
    assert 'svg.addEventListener("pointermove", pointerMove)' in script
    assert 'document.addEventListener("visibilitychange"' in script
    assert 'setTimeout(function () { load(false); }' in script
    assert 'setInterval(updateCountdowns, 1000)' in script
    assert 'AbortController' in script
    assert 'fill="#fbfcfe"' in script
    assert 'stroke="#7467e8"' in script
    assert 'stroke="#087f83"' in script
    assert 'function renderRecords(data)' in script
    assert 'MAX_VISIBLE_DOTS = 32' in script
    assert 'Math.ceil(validIndices.length / MAX_VISIBLE_DOTS)' in script
    assert 'function weeklyChangeEvents()' in script
    assert 'function selectWeeklyGuides(events, start, end)' in script
    assert 'MIN_EVENT_LABEL_GAP = 92' in script
    assert 'codex-week-event-line' in script
    assert 'circlesFor("weekly_remaining"' not in script
    assert '周额度变化 · 标注 ' in script
    assert 'event.clientY - frameRect.top' in script
    assert 'positionTooltip(nearestIndex(ts), event)' in script
    assert 'tooltip.style.top = "16px"' not in script
    assert '.codex-dashboard.is-week-only [data-quota="five_hour"]' in styles
    assert '.codex-week-event-guide text' in styles
    assert '.codex-week-event-node.is-reset' in styles
    assert 'React' not in script
    assert 'new Chart(' not in script
