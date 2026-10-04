"""Debounced alerts; delivery errors never expose the Telegram credential."""
import json
import re
import tempfile
import time
import urllib.request
from pathlib import Path


def next_state(previous, healthy, now):
    state = dict(previous)
    failures = 0 if healthy else previous.get('failures', 0) + 1
    state['failures'] = failures
    active = failures >= 3
    was_active = previous.get('active', False)
    # Preserve an active alarm until an actual healthy observation.
    state['active'] = active or (was_active and not healthy)
    event = None
    if state['active'] and (not was_active or now - previous.get('sent_at', 0) >= 3600):
        event = 'failure'
    elif was_active and healthy:
        event = 'recovery'
    return state, event


def send(message, config):
    success = True
    for admin in config['admin_ids']:
        try:
            request = urllib.request.Request(
                'https://api.telegram.org/bot' + config['token'] + '/sendMessage',
                data=json.dumps({'chat_id': admin, 'text': message}).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=8) as response:
                success = (json.load(response).get('ok') is True) and success
        except Exception:
            print('Telegram alert delivery failed; credentials withheld')
            success = False
    return success and bool(config['admin_ids'])


def read_object(path):
    with path.open(encoding='utf-8') as stream:
        contents = stream.read(65537)
    if len(contents) > 65536:
        raise ValueError('alert file is too large')
    value = json.loads(contents)
    if not isinstance(value, dict):
        raise ValueError('alert file must contain an object')
    return value


def load_config(path):
    try:
        config = read_object(path)
        token = config.get('token')
        admins = config.get('admin_ids')
        if not isinstance(token, str) or not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
            raise ValueError('invalid token')
        if not isinstance(admins, list) or not admins:
            raise ValueError('missing recipients')
        recipients = []
        for admin in admins:
            if isinstance(admin, bool) or not isinstance(admin, (str, int)):
                raise ValueError('invalid recipient')
            if not re.fullmatch(r'-?[0-9]{1,20}', str(admin)) or int(admin) == 0:
                raise ValueError('invalid recipient')
            if int(admin) not in recipients:
                recipients.append(int(admin))
        return {'token': token, 'admin_ids': recipients}
    except (OSError, ValueError, UnicodeError, RecursionError):
        print('Telegram alert configuration is unavailable or invalid; credentials withheld')
        return None


def clean_state(value, now):
    if not isinstance(value, dict):
        return {}
    failures = value.get('failures', 0)
    sent_at = value.get('sent_at', 0)
    return {
        'failures': min(failures, 3) if type(failures) is int and failures >= 0 else 0,
        'active': value.get('active') is True,
        'sent_at': sent_at if type(sent_at) in (int, float) and 0 <= sent_at <= now else 0,
    }


def notify(checks, root=Path('/var/lib/neyro-reserve'), config_path=Path('/etc/neyro-reserve/alerts.json'), *, labels=None, source=None):
    config = load_config(config_path)
    if config is None:
        return
    path = root / 'alert-state.json'
    try:
        previous = read_object(path)
    except FileNotFoundError:
        previous = {}
    except (OSError, ValueError, UnicodeError, RecursionError):
        print('Alert state is unavailable or invalid; restarting observation counters')
        previous = {}
    now = time.time()
    labels = labels or {'primary_healthy': 'основной сервер недоступен', 'backup_fresh': 'нет свежей проверенной резервной копии'}
    result = {'version': 2, 'recipients': {}}
    recipients = previous.get('recipients', {})
    if not isinstance(recipients, dict):
        recipients = {}
    for admin in config['admin_ids']:
        # Migrate the old shared counter without resending an acknowledged alarm.
        old_checks = (previous if 'version' not in previous and 'recipients' not in previous
                      else recipients.get(str(admin), {}))
        if not isinstance(old_checks, dict):
            old_checks = {}
        result['recipients'][str(admin)] = current_checks = {}
        for key, label in labels.items():
            old = clean_state(old_checks.get(key), now)
            current, event = next_state(old, checks.get(key) is True, now)
            if event:
                text = ('NeuroPost: ' + label + '. Проверка с резервного сервера. Автоматическое переключение не выполнялось.'
                        if event == 'failure' else 'NeuroPost: восстановлено — ' + ('основной сервер доступен.' if key == 'primary_healthy' else 'проверенная резервная копия свежая.'))
                if source:
                    text = f'NeuroPost [{source}]: ' + (f'требует внимания — {label}.' if event == 'failure' else f'проверка снова в норме — {label}.')
                if send(text, {'token': config['token'], 'admin_ids': [admin]}):
                    current['sent_at'] = now
                else:
                    # Retry only this recipient, including an undelivered recovery.
                    current['active'] = old.get('active', False)
            current_checks[key] = current
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, prefix='.alert-state.', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream)
        temporary.replace(path)
    except OSError:
        print('Unable to save alert state; recipient delivery counters were not persisted')
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                print('Unable to remove temporary alert state')
