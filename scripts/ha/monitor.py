#!/usr/bin/env python3
"""Independent observations only: never promotes nodes or starts workers."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request

from alerts import notify


def fresh_stamp(path, now, max_age=1800):
    try:
        stamp = datetime.strptime(path.read_text().strip(), '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
        return 0 <= now - stamp <= max_age
    except (OSError, ValueError):
        return False


def primary_healthy():
    try:
        with urllib.request.urlopen('https://flipbazar.ru/healthz', timeout=10) as response:
            return response.status == 200 and json.loads(response.read(1024)) == {'ok': True}
    except Exception:
        return False


def observe(backup=False):
    checks = {'primary_healthy': primary_healthy(), 'disk': shutil.disk_usage('/').free >= 3 * 1024**3}
    if backup:
        root = Path('/var/lib/neyro-offsite')
        checks['backup_fresh'] = fresh_stamp(root / 'last-success', time.time())
        try:
            checks['backup_timer'] = subprocess.run(['systemctl', 'is-active', '--quiet', 'neyro-offsite.timer'], timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            checks['backup_timer'] = False
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup', action='store_true')
    parser.add_argument('--observe-only', action='store_true')
    args = parser.parse_args()
    checks = observe(args.backup)
    root = Path('/var/lib/neyro-independent-monitor')
    root.mkdir(mode=0o700, exist_ok=True)
    temporary = root / 'status.tmp'
    temporary.write_text(json.dumps({'checked_at': int(time.time()), 'checks': checks}) + '\n')
    temporary.replace(root / 'status.json')
    if not args.observe_only:
        labels = {'primary_healthy': 'HTTPS рабочего приложения', 'disk': 'свободное место на резерве',
                  'backup_fresh': 'свежая проверенная копия с образами', 'backup_timer': 'таймер резервного копирования'}
        notify(checks, root=root, config_path=Path('/etc/neyro-independent-monitor/alerts.json'),
               source=socket.gethostname(), labels={key: labels[key] for key in checks})
    print(json.dumps(checks))


if __name__ == '__main__':
    main()
