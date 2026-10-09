#!/usr/bin/env python3
"""Root-owned release worker. Installed via Ansible, never executed from an upload."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path('/opt/neyro')
STATE = Path('/var/lib/neyro-deploy')
LOCKS = Path('/run/lock')
RESERVE_MARKER = Path('/etc/neyro-reserve')
OPERATIONS = Path('/etc/neyro-ops')
REPOSITORY = 'IzmestevIvan/Neyro_posting'
SCHEMA_FILES = ('app/db.py', 'app/billing/schema.py')
SYNC_PATHS = ('app', 'scripts', 'docs', 'Dockerfile', 'requirements.txt', 'requirements.lock', 'requirements-dev.txt', '.dockerignore', 'README.md')
ROOT_FILES = set(SYNC_PATHS) - {'app', 'scripts', 'docs'}


def check_sha(value):
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('A full lowercase Git SHA is required')
    return value


def validate_bundle(archive, commit):
    """Inspect every member before writing anything, including its digest."""
    check_sha(commit)
    files = {}
    total = 0
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or str(path) != member.name or member.name in files or member.size > 8 * 1024 * 1024):
                raise ValueError('Unsafe release member')
            if member.name != '_release.json' and member.name not in ROOT_FILES and not member.name.startswith(('app/', 'scripts/', 'docs/')):
                raise ValueError('Unexpected release path')
            if any(part.startswith('.env') or part in {'.app.env', '.support.env', '.billing.env', '.deploy.env'} for part in path.parts):
                raise ValueError('Environment files are forbidden')
            total += member.size
            if total > 64 * 1024 * 1024:
                raise ValueError('Release is too large')
            files[member.name] = source.extractfile(member).read()
    metadata = json.loads(files.pop('_release.json'))
    if metadata.get('commit') != commit:
        raise ValueError('Wrong release commit')
    actual = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    if metadata.get('files') != actual:
        raise ValueError('Release digest mismatch')
    for required in ('Dockerfile', 'requirements.lock', 'app/main.py', *SCHEMA_FILES):
        if required not in files:
            raise ValueError('Incomplete release')
    return files


def github_json(path):
    request = urllib.request.Request('https://api.github.com/repos/'+REPOSITORY+'/'+path,
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'NeyroPost-deploy',
                 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def verify_approved_source(commit, files):
    # The public repository lets the server validate without holding a GitHub token.
    # A stolen upload key cannot replace source, bypass CI, or choose a fork.
    if github_json('git/ref/heads/main')['object']['sha'] != commit:
        raise ValueError('Only the current main commit may be released')
    runs = github_json(f'actions/workflows/ci.yml/runs?head_sha={commit}&branch=main&event=push')['workflow_runs']
    if not runs or runs[0]['head_sha'] != commit or runs[0]['conclusion'] != 'success':
        raise ValueError('Successful main CI is required')
    request = urllib.request.Request(f'https://codeload.github.com/{REPOSITORY}/tar.gz/{commit}',
                                     headers={'User-Agent': 'NeyroPost-deploy'})
    with urllib.request.urlopen(request, timeout=60) as response:
        archive = response.read(32 * 1024 * 1024 + 1)
    if len(archive) > 32 * 1024 * 1024:
        raise ValueError('Repository archive is too large')
    expected = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
        total = 0
        for member in source:
            name = member.name.partition('/')[2]
            if member.isdir() or (name not in ROOT_FILES and not name.startswith(('app/', 'scripts/', 'docs/'))):
                continue
            total += member.size
            if not member.isfile() or member.size > 8 * 1024 * 1024 or total > 64 * 1024 * 1024:
                raise ValueError('Invalid committed release archive')
            expected[name] = hashlib.sha256(source.extractfile(member).read()).hexdigest()
    if expected != {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}:
        raise ValueError('Upload differs from the approved GitHub commit')


def write_status(commit, status, phase, **extra):
    path = STATE / 'status' / f'{commit}.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'commit': commit, 'status': status, 'phase': phase, **extra})+'\n')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def run(args, **kwargs):
    return subprocess.run(args, check=True, cwd=ROOT, timeout=600, **kwargs)


def capture(args):
    return subprocess.check_output(args, cwd=ROOT, text=True, timeout=30).strip()


def compose(*args):
    return ['docker', 'compose', '-p', 'neyro', '-f', str(ROOT / 'docker-compose.yml'), *args]


def synchronize(source):
    for name in SYNC_PATHS:
        origin, target = source / name, ROOT / name
        if not origin.exists():
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            continue
        if target.is_dir():
            shutil.rmtree(target)
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            shutil.copy2(origin, target)


def verify_health():
    config = json.loads(Path('/etc/neyro-deploy/config.json').read_text())
    url = config['health_url']
    if not url.startswith('https://'):
        raise ValueError('Public health check requires HTTPS')
    with urllib.request.urlopen(url, timeout=15) as response:
        if response.status != 200 or json.load(response) != {'ok': True}:
            raise RuntimeError('Public health check failed')


def wait_for_app_health(timeout=150, stable_seconds=10):
    """Require both workers to remain up while HTTP health stays green."""
    deadline = time.monotonic() + timeout
    stable_since = None
    identity = None
    while True:
        support = json.loads(capture(['docker', 'inspect', '--format',
            '{"id":{{json .Id}},"status":{{json .State.Status}},"restarts":{{.RestartCount}}}',
            'neyro-support-1']))
        if support.get('status') != 'running' or support.get('restarts') != 0:
            raise RuntimeError('Support worker stopped or restarted during release')
        current_identity = support.get('id')
        if not current_identity:
            raise RuntimeError('Support worker identity is missing')
        if identity is not None and current_identity != identity:
            raise RuntimeError('Support worker was replaced during release')
        identity = current_identity
        healthy = capture(['docker', 'inspect', '--format', '{{.State.Health.Status}}', 'neyro-app-1']) == 'healthy'
        now = time.monotonic()
        stable_since = (now if stable_since is None else stable_since) if healthy else None
        if stable_since is not None and now - stable_since >= stable_seconds:
            return
        if now >= deadline:
            raise RuntimeError('Application/worker health check timed out')
        time.sleep(1)


def restart_workers(override=None):
    arguments = [] if override is None else ['-f', str(override)]
    run(compose(*arguments, 'up', '-d', '--no-build', '--no-deps', 'app', 'support'))
    wait_for_app_health()


def rollback(backup, old_images):
    synchronize(backup / 'source')
    run(['docker', 'tag', old_images['app'], 'neyro-posting:latest'])
    override = backup / 'rollback.json'
    override.write_text(json.dumps({'services': {name: {'image': image} for name, image in old_images.items()}}))
    restart_workers(override)
    verify_health()


def deploy(commit):
    check_sha(commit)
    # Shared with manual releases. A second job fails before changing the stack.
    with open(LOCKS / 'neyro-deploy.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_status(commit, 'running', 'validate')
        files = validate_bundle(STATE / 'uploads' / f'{commit}.tar.gz', commit)
        verify_approved_source(commit, files)
        if RESERVE_MARKER.exists() and not (
                (RESERVE_MARKER / 'ACTIVE').is_file()
                and json.loads((OPERATIONS / 'config.json').read_text()).get('role') == 'primary'):
            raise RuntimeError('Deployment on a reserve is forbidden')
        if any((OPERATIONS / name).exists() for name in ('FENCED', 'maintenance')):
            raise RuntimeError('Release blocked by the operations maintenance/fencing guard')
        for name in SCHEMA_FILES:
            if (ROOT / name).read_bytes() != files[name]:
                raise RuntimeError('Schema changes require a separate reviewed migration')
        destination = ROOT / 'releases' / commit
        if destination.exists():
            raise RuntimeError('Release directory already exists; use a new commit')
        destination.mkdir(mode=0o700, parents=True)
        for name, data in files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        old_images = {service: capture(['docker', 'inspect', '--format', '{{.Image}}', f'neyro-{service}-1']) for service in ('app', 'support')}
        backup = Path(tempfile.mkdtemp(prefix=f'ci-{commit[:12]}-', dir=ROOT / 'backups'))
        (backup / 'source').mkdir()
        for name in SYNC_PATHS:
            origin = ROOT / name
            if origin.is_dir():
                shutil.copytree(origin, backup / 'source' / name)
            elif origin.exists():
                shutil.copy2(origin, backup / 'source' / name)
        (backup / 'images.json').write_text(json.dumps(old_images))
        write_status(commit, 'running', 'backup')
        with open(backup / 'snapshot.tar.gz.age', 'wb') as output:
            run(['/usr/local/sbin/neyro-export-backup'], env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'snapshot'}, stdout=output)
        if (backup / 'snapshot.tar.gz.age').stat().st_size == 0:
            raise RuntimeError('Empty backup')
        image = f'neyro-posting:{commit}'
        write_status(commit, 'running', 'build')
        run(['docker', 'build', '--label', f'org.opencontainers.image.revision={commit}', '-t', image, str(destination)])
        run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', image,
             '-c', 'import os,asyncpg,fastapi,aiogram; assert os.getuid() == 10001'])
        # Freeze source/image identity while snapshots are being exported.
        export_lock = open(LOCKS / 'neyro-export.lock', 'w')
        fcntl.flock(export_lock, fcntl.LOCK_EX)
        try:
            write_status(commit, 'running', 'activate')
            synchronize(destination)
            run(['docker', 'tag', image, 'neyro-posting:latest'])
            restart_workers()
            verify_health()
        except BaseException:
            write_status(commit, 'running', 'rollback')
            rollback(backup, old_images)
            write_status(commit, 'failed', 'rolled_back', backup=str(backup))
            raise
        finally:
            export_lock.close()
        (destination / 'git-revision').write_text(commit+'\n')
        (STATE / 'current.json').write_text(json.dumps({'commit': commit, 'image': capture(['docker', 'image', 'inspect', '--format', '{{.Id}}', image]), 'backup': str(backup)}))
        write_status(commit, 'success', 'healthy', backup=str(backup))


if __name__ == '__main__':
    os.umask(0o077)
    def terminate(signum, frame):
        raise SystemExit('Release interrupted')
    signal.signal(signal.SIGTERM, terminate)
    if len(sys.argv) != 3 or sys.argv[1] != 'deploy':
        raise SystemExit('Usage: neyro-release.py deploy <full-sha>')
    sha = check_sha(sys.argv[2])
    try:
        deploy(sha)
    except BaseException:
        current = STATE / 'status' / f'{sha}.json'
        if not current.exists() or json.loads(current.read_text()).get('phase') != 'rolled_back':
            write_status(sha, 'failed', 'see_server_log')
        raise
