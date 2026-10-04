#!/usr/bin/env python3
"""Package an exact Git commit; never include the working directory or secrets."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT_FILES = {'Dockerfile', 'requirements.txt', 'requirements.lock', 'requirements-dev.txt', '.dockerignore', 'README.md'}
PREFIXES = ('app/', 'scripts/', 'docs/')


def build_bundle(commit, destination):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('A full commit SHA is required')
    archive = subprocess.check_output(['git', 'archive', '--format=tar', commit])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if member.isdir():
                continue
            if member.name not in ROOT_FILES and not member.name.startswith(PREFIXES):
                continue
            if not member.isfile():
                raise ValueError('Release files must not be links')
            files[member.name] = source.extractfile(member).read()
    metadata = {'commit': commit, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files['_release.json'] = json.dumps(metadata, sort_keys=True).encode()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, 'w:gz') as target:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            target.addfile(member, io.BytesIO(data))
    print(hashlib.sha256(destination.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('commit')
    parser.add_argument('destination')
    args = parser.parse_args()
    build_bundle(args.commit, args.destination)
