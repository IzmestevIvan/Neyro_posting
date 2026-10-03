import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('resilience_alerts', Path(__file__).resolve().parents[1] / 'scripts/resilience/alerts.py')
alerts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alerts)


def test_failure_requires_three_consecutive_observations():
    state = {}
    for now in (1, 2):
        state, event = alerts.next_state(state, False, now)
        assert event is None
    state, event = alerts.next_state(state, False, 3)
    assert event == 'failure'
    state['sent_at'] = 3
    state, event = alerts.next_state(state, False, 4)
    assert event is None
    state, event = alerts.next_state(state, True, 5)
    assert event == 'recovery'
    assert not state['active']


def test_transient_failure_does_not_notify_and_reminders_are_bounded():
    state, _ = alerts.next_state({}, False, 1)
    state, event = alerts.next_state(state, True, 2)
    assert state['failures'] == 0 and event is None
    state = {'failures': 10, 'active': True, 'sent_at': 100}
    _, event = alerts.next_state(state, False, 3699)
    assert event is None
    _, event = alerts.next_state(state, False, 3700)
    assert event == 'failure'


@pytest.fixture
def alert_config(tmp_path):
    path = tmp_path / 'alerts.json'
    path.write_text(json.dumps({'token': '123456:TEST_SECRET', 'admin_ids': [101, 202]}))
    return path


def test_blocked_recipient_does_not_repeat_alerts_for_other_admins(tmp_path, alert_config, monkeypatch):
    now = [1000]
    calls = []
    monkeypatch.setattr(alerts.time, 'time', lambda: now[0])

    def deliver(message, config):
        admin = config['admin_ids'][0]
        calls.append((admin, message))
        return admin == 101

    monkeypatch.setattr(alerts, 'send', deliver)
    checks = {'primary_healthy': False, 'backup_fresh': True}
    for _ in range(3):
        alerts.notify(checks, tmp_path, alert_config)
        now[0] += 60
    assert [admin for admin, _ in calls] == [101, 202]
    calls.clear()
    alerts.notify(checks, tmp_path, alert_config)
    assert [admin for admin, _ in calls] == [202]
    state = json.loads((tmp_path / 'alert-state.json').read_text())
    assert state['recipients']['101']['primary_healthy']['active'] is True
    assert state['recipients']['202']['primary_healthy']['active'] is False
    assert (tmp_path / 'alert-state.json').stat().st_mode & 0o777 == 0o600
    calls.clear()
    now[0] += 3600
    alerts.notify(checks, tmp_path, alert_config)
    assert [admin for admin, _ in calls] == [101, 202]


def test_failed_recovery_is_retried_only_for_its_recipient(tmp_path, alert_config, monkeypatch):
    monkeypatch.setattr(alerts.time, 'time', lambda: 1000)
    monkeypatch.setattr(alerts, 'send', lambda *args: True)
    for _ in range(3):
        alerts.notify({'primary_healthy': False, 'backup_fresh': True}, tmp_path, alert_config)
    calls = []

    def deliver(message, config):
        calls.append(config['admin_ids'][0])
        return config['admin_ids'][0] == 101

    monkeypatch.setattr(alerts, 'send', deliver)
    healthy = {'primary_healthy': True, 'backup_fresh': True}
    alerts.notify(healthy, tmp_path, alert_config)
    assert calls == [101, 202]
    calls.clear()
    alerts.notify(healthy, tmp_path, alert_config)
    assert calls == [202]
    monkeypatch.setattr(alerts, 'send', lambda *args: True)
    alerts.notify(healthy, tmp_path, alert_config)
    monkeypatch.setattr(alerts, 'send', lambda *args: pytest.fail('recovery already delivered'))
    alerts.notify(healthy, tmp_path, alert_config)


def test_shared_state_migrates_without_repeating_recent_alarm(tmp_path, alert_config, monkeypatch):
    (tmp_path / 'alert-state.json').write_text(json.dumps({
        'primary_healthy': {'active': True, 'failures': 3, 'sent_at': 900}}))
    monkeypatch.setattr(alerts.time, 'time', lambda: 1000)
    monkeypatch.setattr(alerts, 'send', lambda *args: pytest.fail('recent alarm already delivered'))
    alerts.notify({'primary_healthy': False, 'backup_fresh': True}, tmp_path, alert_config)
    state = json.loads((tmp_path / 'alert-state.json').read_text())
    assert set(state['recipients']) == {'101', '202'}
    assert state['recipients']['101']['primary_healthy']['sent_at'] == 900


@pytest.mark.parametrize('contents', [
    'bad-json-secret', '[]', '{"token": "secret", "admin_ids": [101]}',
    '{"token": "123:SECRET", "admin_ids": "101"}',
    '{"token": "123:SECRET", "admin_ids": [true]}',
])
def test_invalid_configuration_does_not_send_or_leak_contents(tmp_path, monkeypatch, capsys, contents):
    config = tmp_path / 'alerts.json'
    config.write_text(contents)
    monkeypatch.setattr(alerts, 'send', lambda *args: pytest.fail('invalid configuration must not send'))
    alerts.notify({}, tmp_path, config)
    output = capsys.readouterr().out
    assert 'configuration is unavailable or invalid' in output
    assert contents not in output and 'SECRET' not in output
    assert not (tmp_path / 'alert-state.json').exists()


@pytest.mark.parametrize('contents', [
    'bad-state-secret', '[]', '{"version": 2, "recipients": []}',
    '{"version": 2, "recipients": {"101": null, "202": {"primary_healthy": {"failures": "bad", "active": "bad", "sent_at": []}}}}',
])
def test_corrupt_state_restarts_debounce_safely(tmp_path, alert_config, monkeypatch, capsys, contents):
    (tmp_path / 'alert-state.json').write_text(contents)
    calls = []
    monkeypatch.setattr(alerts, 'send', lambda message, config: calls.append(config['admin_ids'][0]) or True)
    checks = {'primary_healthy': False, 'backup_fresh': True}
    alerts.notify(checks, tmp_path, alert_config)
    assert not calls
    alerts.notify(checks, tmp_path, alert_config)
    assert not calls
    alerts.notify(checks, tmp_path, alert_config)
    assert calls == [101, 202]
    assert contents not in capsys.readouterr().out


def test_delivery_exception_does_not_expose_token(monkeypatch, capsys):
    def failed_request(*args, **kwargs):
        raise ValueError('https://api.telegram.org/bot123456:TEST_SECRET/sendMessage')

    monkeypatch.setattr(alerts.urllib.request, 'urlopen', failed_request)
    assert not alerts.send('test', {'token': '123456:TEST_SECRET', 'admin_ids': [101]})
    assert 'TEST_SECRET' not in capsys.readouterr().out


def test_duplicate_recipient_is_contacted_once(tmp_path, alert_config, monkeypatch):
    alert_config.write_text(json.dumps({'token': '123:SECRET', 'admin_ids': [101, '101']}))
    calls = []
    monkeypatch.setattr(alerts, 'send', lambda message, config: calls.append(config['admin_ids'][0]) or True)
    for _ in range(3):
        alerts.notify({'primary_healthy': False, 'backup_fresh': True}, tmp_path, alert_config)
    assert calls == [101]
