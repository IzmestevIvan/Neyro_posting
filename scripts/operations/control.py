#!/usr/bin/env python3
"""Explicit administrator operations sharing the deployment/recovery lock."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess

CONFIG = Path('/etc/neyro-ops')
LOCK = Path('/run/lock/neyro-deploy.lock')


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120).stdout.strip()


def apply(action):
    os.umask(0o077)
    role = json.loads((CONFIG / 'config.json').read_text())['role']
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'maintenance_on':
            (CONFIG / 'maintenance').touch(mode=0o600)
        elif action == 'maintenance_off':
            if (CONFIG / 'FENCED').exists():
                raise RuntimeError('Fenced primary requires a separately reviewed failback')
            (CONFIG / 'maintenance').unlink(missing_ok=True)
        elif action in ('restart_app', 'fence_primary'):
            if role != 'primary' or (Path('/etc/neyro-reserve').exists() and not Path('/etc/neyro-reserve/ACTIVE').is_file()):
                raise RuntimeError('Workers must never be started on standby by this tool')
            if action == 'fence_primary':
                # Persist before Docker operations; an interrupted fence stays guarded.
                (CONFIG / 'FENCED').touch(mode=0o600)
                (CONFIG / 'maintenance').touch(mode=0o600)
                run(['docker', 'update', '--restart=no', 'neyro-app-1', 'neyro-support-1'])
                run(['docker', 'stop', '--time', '30', 'neyro-app-1', 'neyro-support-1'])
                for name in ('app', 'support'):
                    if run(['docker', 'inspect', '--format', '{{.State.Running}}', f'neyro-{name}-1']) != 'false':
                        raise RuntimeError('Fencing verification failed')
            else:
                if any((CONFIG / name).exists() for name in ('FENCED', 'maintenance')):
                    raise RuntimeError('Restart blocked by maintenance/fencing')
                if run(['docker', 'inspect', '--format', '{{.State.Running}}', 'neyro-app-1']) != 'true':
                    raise RuntimeError('Refusing to start an intentionally stopped worker')
                if run(['docker', 'inspect', '--format', '{{.State.Health.Status}}', 'neyro-db-1']) != 'healthy':
                    raise RuntimeError('Database must be healthy before restarting the application')
                run(['docker', 'restart', '--time', '30', 'neyro-app-1'])
        else:
            raise ValueError('Unknown action')
    print(json.dumps({'action': action, 'role': role, 'completed': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['maintenance_on', 'maintenance_off', 'restart_app', 'fence_primary'])
    args = parser.parse_args()
    try:
        apply(args.action)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit('Operation refused or failed: ' + type(error).__name__ + '; inspect host state before retrying')
