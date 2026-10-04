"""Fast, value-redacting checks for tracked secrets and dependency drift."""
import re
import subprocess
import sys
from pathlib import Path


def check(staged=False):
    errors = []
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', *([] if staged else ['--others', '--exclude-standard'])]).decode().split('\0')
    patterns = [re.compile(r'(?<![A-Za-z0-9_])live_[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_])'),
                re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----')]
    def content(name):
        if staged:
            return subprocess.check_output(['git', 'show', ':'+name]).decode()
        return Path(name).read_text()

    for name in sorted(set(filter(None, names))):
        path = Path(name)
        if (path.name in {'.env', '.app.env', '.deploy.env', '.billing.env', '.support.env'}
                or name.startswith(('data/', '.kube/')) or path.suffix in {'.key', '.pem'}):
            errors.append(f'{name}: protected file is tracked')
        if not staged and (not path.is_file() or path.stat().st_size > 2_000_000):
            continue
        try:
            lines = content(name).splitlines()
        except UnicodeError:
            continue
        for line_number, line in enumerate(lines, 1):
            if any(pattern.search(line) for pattern in patterns):
                errors.append(f'{name}:{line_number}: possible secret (value redacted)')
    def versions(file):
        return {name.lower().replace('_', '-'): version for name, version in
                re.findall(r'^([\w-]+)(?:\[[^]]+\])?==([^\s]+)$', content(file), re.M)}
    locked = versions('requirements.lock')
    for name, version in versions('requirements.txt').items():
        if locked.get(name) != version:
            errors.append(f'requirements.lock: {name} differs from requirements.txt')
    if errors:
        raise SystemExit('\n'.join(errors))
    print('Tracked files and runtime version pins: OK')


if __name__ == '__main__':
    check(staged='--staged' in sys.argv[1:])
