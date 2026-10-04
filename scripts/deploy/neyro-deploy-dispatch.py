#!/usr/bin/env python3
"""Forced SSH command: bounded uploads, asynchronous release, redacted status only."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

STATE = Path('/var/lib/neyro-deploy')


def parse_command(command):
    parts = command.split()
    if len(parts) not in (2, 3) or not re.fullmatch(r'[0-9a-f]{40}', parts[1]):
        raise ValueError('Expected a full commit SHA')
    action, sha = parts[:2]
    if action == 'upload' and len(parts) == 3 and re.fullmatch(r'[0-9a-f]{64}', parts[2]):
        return action, sha, parts[2]
    if action in {'deploy', 'status'} and len(parts) == 2:
        return action, sha, None
    raise ValueError('Command not allowed')


def main():
    os.umask(0o077)
    signal.alarm(120)
    action, sha, checksum = parse_command(os.getenv('SSH_ORIGINAL_COMMAND', ''))
    if action == 'status':
        path = STATE / 'status' / f'{sha}.json'
        print(path.read_text() if path.exists() else json.dumps({'commit':sha, 'status':'missing'}))
    elif action == 'upload':
        path = STATE / 'status' / f'{sha}.json'
        if path.exists() and json.loads(path.read_text()).get('status') in {'pending', 'running', 'success'}:
            raise ValueError('Cannot replace a submitted release')
        fd, temporary = tempfile.mkstemp(dir=STATE / 'uploads', prefix='.upload-')
        try:
            digest = hashlib.sha256()
            total = 0
            with os.fdopen(fd, 'wb') as target:
                while chunk := sys.stdin.buffer.read(65536):
                    total += len(chunk)
                    if total > 16 * 1024 * 1024:
                        raise ValueError('Upload exceeds 16 MiB')
                    digest.update(chunk)
                    target.write(chunk)
            if digest.hexdigest() != checksum:
                raise ValueError('Checksum mismatch')
            os.replace(temporary, STATE / 'uploads' / f'{sha}.tar.gz')
            print('Upload verified')
        finally:
            Path(temporary).unlink(missing_ok=True)
    else:
        if not (STATE / 'uploads' / f'{sha}.tar.gz').is_file():
            raise ValueError('Upload required')
        dispatch_lock = open(STATE / 'dispatch.lock', 'w')
        fcntl.flock(dispatch_lock, fcntl.LOCK_EX)
        status_path = STATE / 'status' / f'{sha}.json'
        if status_path.exists() and json.loads(status_path.read_text()).get('status') in {'pending', 'running', 'success'}:
            raise ValueError('Release already submitted')
        status_path.write_text(json.dumps({'commit':sha,'status':'pending','phase':'queued'})+'\n')
        try:
            subprocess.run(['systemd-run', '--quiet', '--collect', f'--unit=neyro-release-{sha[:12]}-{int(time.time())}',
                            '--property=Type=exec', '--property=RuntimeMaxSec=1800',
                            f'--property=StandardOutput=append:/var/log/neyro-deploy/{sha}.log',
                            f'--property=StandardError=append:/var/log/neyro-deploy/{sha}.log',
                            '/usr/local/sbin/neyro-release.py', 'deploy', sha], check=True)
        except Exception:
            status_path.write_text(json.dumps({'commit':sha,'status':'failed','phase':'could_not_start'})+'\n')
            raise
        print('Release queued')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Deployment command rejected; inspect the server log.', file=sys.stderr)
        sys.exit(1)
