#!/usr/bin/env python3
"""External-to-primary observations; never promotes the reserve on timeouts."""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

root = Path('/var/lib/neyro-reserve')
active = Path('/etc/neyro-reserve/ACTIVE').exists()
checks = {'checked_at': datetime.now(timezone.utc).isoformat(), 'role': 'active' if active else 'reserve'}
try:
    url = Path('/etc/neyro-reserve/active-health-url' if active else '/etc/neyro-reserve/health-url').read_text().strip()
    with urllib.request.urlopen(url, timeout=10) as response:
        checks['primary_healthy'] = response.status == 200 and json.load(response).get('ok') is True
except Exception as error:
    checks['primary_healthy'] = False
    checks['primary_error'] = type(error).__name__
try:
    stamp = (root / ('active-offsite-last-success' if active else 'last-success')).read_text().strip()
    timestamp = datetime.strptime(stamp, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
    checks['backup_age_seconds'] = int(time.time() - timestamp)
    checks['backup_fresh'] = 0 <= checks['backup_age_seconds'] <= 30 * 60
except (OSError, ValueError):
    checks['backup_fresh'] = False
checks['action_required'] = not (checks['primary_healthy'] and checks['backup_fresh'])
temp = root / 'health-status.tmp'
temp.write_text(json.dumps(checks, indent=2) + '\n')
os.chmod(temp, 0o600)
temp.replace(root / 'health-status.json')
from neyro_alerts import notify
notify(checks, source='active' if active else None, labels={'primary_healthy': 'активное приложение недоступно', 'backup_fresh': 'нет свежей независимой копии активной базы'} if active else None)
print(json.dumps(checks))
raise SystemExit(int(checks['action_required']))
