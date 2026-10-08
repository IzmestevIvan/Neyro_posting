#!/usr/bin/env python3
"""Bounded local observations and recovery. Never starts a stopped worker or DB."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

CONFIG = Path('/etc/neyro-ops')
STATE = Path('/var/lib/neyro-ops')
DEPLOY_LOCK = Path('/run/lock/neyro-deploy.lock')
# Pending maintenance is actionable, but unchanged hourly reminders hide outages.
REMINDER_INTERVALS = {'reboot_clear': 24 * 3600}
LABELS = {
    'disk': 'свободное место на диске', 'memory': 'доступная память',
    'database': 'здоровье базы данных', 'application': 'здоровье приложения',
    'support': 'работа поддержки', 'proxy': 'работа HTTPS-прокси',
    'security': 'работа SSH, firewall, Fail2ban и аудита',
    'workers_stopped': 'отсутствие работающих ботов на резерве',
    'backup_timer': 'таймеры резервирования и внешнего мониторинга',
    'reboot_clear': 'плановая перезагрузка после обновлений безопасности',
    'known_containers': 'отсутствие посторонних работающих контейнеров',
    'maintenance_clear': 'завершение обслуживания сервера',
}


def run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True).stdout.strip()


def object_file(path):
    try:
        if path.stat().st_size > 131072:
            return {}
        obj = json.loads(path.read_text())
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def save(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def observe(role):
    checks, details = {}, {}
    usage = shutil.disk_usage('/opt/neyro')
    nodes = os.statvfs('/opt/neyro')
    checks['disk'] = usage.free >= 3 * 1024**3 and usage.free / usage.total >= .12 and nodes.f_favail / max(nodes.f_files, 1) >= .1
    details['disk_free_mib'] = usage.free // 1024**2
    info = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    details['memory_available_mib'] = int(info['MemAvailable'].split()[0]) // 1024
    checks['memory'] = details['memory_available_mib'] >= 160
    containers = {}
    # Request only state fields; Docker inspect's full output contains secrets.
    for name in ('app', 'support', 'db', 'caddy'):
        try:
            state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', f'neyro-{name}-1']))
            containers[name] = {'status': state.get('Status'), 'health': state.get('Health', {}).get('Status')}
        except (subprocess.SubprocessError, OSError, ValueError):
            containers[name] = {'status': 'unknown', 'health': None}
    details['containers'] = containers
    checks['database'] = containers['db'] == {'status': 'running', 'health': 'healthy'}
    if role == 'primary':
        checks['application'] = containers['app'] == {'status': 'running', 'health': 'healthy'}
        checks['support'] = containers['support']['status'] == 'running'
        checks['proxy'] = containers['caddy']['status'] == 'running'
    else:
        # Treat Docker errors as unknown, not proof that standby workers are off.
        try:
            active = run(['docker', 'ps', '--format', '{{.Names}}']).splitlines()
            checks['workers_stopped'] = not set(active).intersection({'neyro-app-1', 'neyro-support-1', 'neyro-caddy-1'})
        except (subprocess.SubprocessError, OSError):
            checks['workers_stopped'] = False
        try:
            run(['systemctl', 'is-active', 'neyro-backup.timer', 'neyro-monitor.timer'])
            checks['backup_timer'] = True
        except (subprocess.SubprocessError, OSError):
            checks['backup_timer'] = False
    try:
        active = run(['docker', 'ps', '--format', '{{.Names}}']).splitlines()
        allowed = {'neyro-app-1', 'neyro-support-1', 'neyro-db-1', 'neyro-caddy-1'}
        checks['known_containers'] = all(name in allowed or name.startswith('neyro-restore-check-') for name in active)
    except (subprocess.SubprocessError, OSError):
        checks['known_containers'] = False
    try:
        run(['systemctl', 'is-active', 'docker', 'ssh', 'fail2ban', 'auditd', 'neyro-docker-ingress'])
        run(['/usr/local/sbin/neyro-docker-ingress', '--check'])
        ssh = run(['/usr/sbin/sshd', '-T'])
        checks['security'] = (run(['ufw', 'status']).startswith('Status: active')
                              and 'passwordauthentication no' in ssh
                              and 'permitrootlogin prohibit-password' in ssh
                              and 'kbdinteractiveauthentication no' in ssh)
        # OpenSSH spells prohibit-password as without-password in some versions.
        if 'permitrootlogin without-password' in ssh:
            checks['security'] = (run(['ufw', 'status']).startswith('Status: active')
                                  and 'passwordauthentication no' in ssh
                                  and 'kbdinteractiveauthentication no' in ssh)
    except (subprocess.SubprocessError, OSError):
        checks['security'] = False
    checks['reboot_clear'] = not Path('/var/run/reboot-required').exists()
    maintenance = CONFIG / 'maintenance'
    details['maintenance'] = maintenance.exists() or (CONFIG / 'FENCED').exists()
    checks['maintenance_clear'] = not details['maintenance']
    if details['maintenance']:
        # Planned maintenance suppresses service alarms, but remains visible itself.
        for name in ('application', 'support', 'proxy'):
            if name in checks:
                checks[name] = True
    return checks, details


def recovery_decision(previous, checks, details, now, role, enabled):
    """Pure policy: fail closed on corrupt state or missing preconditions."""
    history = previous.get('attempts', [])
    counter = previous.get('failures', 0)
    if (not isinstance(history, list) or any(type(t) not in (int, float) or not 0 <= t <= now for t in history)
            or type(counter) is not int or not 0 <= counter <= 3):
        return {'failures': 0, 'attempts': []}, False
    history = [t for t in history if now - t < 3600]
    eligible = (role == 'primary' and enabled is True and not details.get('maintenance', True)
                and all(checks.get(k) is True for k in ('database', 'disk', 'memory', 'security'))
                and details.get('containers', {}).get('app') == {'status': 'running', 'health': 'unhealthy'})
    failures = min(counter + 1, 3) if eligible else 0
    action = failures >= 3 and len(history) < 2 and (not history or now - history[-1] >= 900)
    return {'failures': failures, 'attempts': history}, action


def recover(state, config):
    # Share the release/fencing lock and re-observe immediately before the action.
    with DEPLOY_LOCK.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'deployment_in_progress'
        checks, details = observe(config['role'])
        _, allowed = recovery_decision(state, checks, details, time.time(), config['role'], config.get('auto_recover'))
        if not allowed or (Path('/etc/neyro-reserve').exists() and not Path('/etc/neyro-reserve/ACTIVE').is_file()):
            return 'guarded'
        # Persist before the attempt. An interrupted restart still consumes budget.
        state['attempts'].append(time.time())
        state['failures'] = 0
        save(STATE / 'recovery.json', state)
        try:
            run(['docker', 'restart', '--time', '30', 'neyro-app-1'], timeout=60)
            return 'app_restart_requested'
        except (subprocess.SubprocessError, OSError):
            return 'app_restart_failed'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--observe-only', action='store_true', help='No alerts, state writes or recovery')
    args = parser.parse_args()
    os.umask(0o077)
    config = object_file(CONFIG / 'config.json')
    if config.get('role') not in ('primary', 'reserve'):
        raise SystemExit('Invalid operations configuration')
    checks, details = observe(config['role'])
    result = {'checked_at': int(time.time()), 'role': config['role'], 'checks': checks, **details}
    if not args.observe_only:
        with (STATE / 'monitor.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            state, action = recovery_decision(object_file(STATE / 'recovery.json'), checks, details,
                                               time.time(), config['role'], config.get('auto_recover'))
            save(STATE / 'recovery.json', state)
            if action:
                result['recovery_action'] = recover(state, config)
                print('NeuroPost recovery: ' + result['recovery_action'], flush=True)
            save(STATE / 'status.json', result)
            from neyro_alerts import notify
            notify(checks, STATE, CONFIG / 'alerts.json', labels={k: LABELS[k] for k in checks},
                   source=config['role'], reminder_intervals=REMINDER_INTERVALS)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
