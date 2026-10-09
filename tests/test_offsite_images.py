"""Exercise the restricted SSH endpoint without Docker or production paths."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('command', ['image latest', 'image sha256:' + 'a' * 63,
                                    'image sha256:' + 'a' * 64 + '; id',
                                    'image sha256:' + 'A' * 64, 'image /etc/passwd', 'sh'])
def test_endpoint_rejects_untrusted_commands(command):
    result = subprocess.run(['bash', 'scripts/ha/backup-endpoint.sh'],
                            env={**os.environ, 'SSH_ORIGINAL_COMMAND': command}, capture_output=True)
    assert result.returncode == 64
    assert result.stdout == b''


def test_endpoint_exports_only_exact_image_id(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name, content in {
        'flock': '#!/bin/sh\nexit 0\n',
        'docker': '#!/bin/sh\nprintf "%s\\n" "$@"\n',
        'age': '#!/bin/sh\ncat\n',
    }.items():
        target = bin_dir / name
        target.write_text(content)
        target.chmod(0o700)
    (tmp_path / 'recipient').write_text('fixture-recipient')
    script = Path('scripts/ha/backup-endpoint.sh').read_text()
    script = script.replace('/run/lock/neyro-export.lock', str(tmp_path / 'lock'))
    script = script.replace('/etc/neyro-backup/recipient', str(tmp_path / 'recipient'))
    image_id = 'sha256:' + 'a' * 64
    result = subprocess.run(['bash', '-c', script], env={**os.environ,
        'PATH': str(bin_dir) + ':' + os.environ['PATH'], 'SSH_ORIGINAL_COMMAND': 'image ' + image_id},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ['image', 'save', image_id]
