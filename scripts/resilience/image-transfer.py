#!/usr/bin/env python3
"""Content-verified Docker archive deltas over the existing encrypted backup pipe.

No tar member is extracted to a filesystem path. Import reconstructs a Docker save
archive, validates every reused/new entry and finally requires the expected image ID.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import tarfile
import tempfile

LIMIT = 2 * 1024**3
METADATA_LIMIT = 1024**2


def image_id(value):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ValueError('Invalid image ID')
    return value


def docker(*args):
    return subprocess.check_output(['docker', *args], timeout=180, stderr=subprocess.DEVNULL).decode().strip()


def save_image(image, path):
    image_id(image)
    subprocess.run(['docker', 'image', 'save', '-o', str(path), image], timeout=180,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if path.stat().st_size > LIMIT:
        raise ValueError('Image exceeds transfer limit')


def members(source):
    result, total = {}, 0
    for member in source:
        name = member.name
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or '..' in path.parts or str(path) != name.rstrip('/')
                or not (member.isfile() or member.isdir()) or name in result):
            raise ValueError('Unsafe or duplicated image member')
        total += member.size
        if total > LIMIT or len(result) >= 10000:
            raise ValueError('Image archive exceeds limits')
        result[name] = member
    return result


def digest(source, member):
    if not member.isfile():
        return None
    result = hashlib.sha256()
    with source.extractfile(member) as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def create(base_path, target_path, output, base_id, target_id):
    with tarfile.open(target_path) as target:
        target_members = members(target)
        with tarfile.open(base_path) if base_path else tarfile.open(fileobj=io.BytesIO(bytes(10240))) as base:
            base_members = members(base)
            records = []
            for name, member in target_members.items():
                checksum = digest(target, member)
                old = base_members.get(name)
                reuse = bool(member.isfile() and old and old.isfile() and old.size == member.size and digest(base, old) == checksum)
                records.append({'name': name, 'size': member.size, 'sha256': checksum, 'reuse': reuse})
            metadata = json.dumps({'version': 1, 'base': base_id, 'target': target_id, 'members': records}).encode()
            if len(metadata) > METADATA_LIMIT:
                raise ValueError('Image metadata exceeds limit')
            # gzip compresses new layers; common layers consume only metadata bytes.
            with tarfile.open(fileobj=output, mode='w|gz', compresslevel=1) as archive:
                info = tarfile.TarInfo('transfer.json')
                info.size = len(metadata)
                archive.addfile(info, io.BytesIO(metadata))
                for record in records:
                    if not record['reuse']:
                        source = target_members[record['name']]
                        copy = tarfile.TarInfo('payload/' + source.name)
                        copy.type, copy.size, copy.mode = source.type, source.size, source.mode
                        archive.addfile(copy, target.extractfile(source) if source.isfile() else None)


def assemble(base_path, package_path, destination, expected_base, expected_target):
    with tarfile.open(package_path, mode='r:gz') as package:
        package_members = members(package)
        info = package_members.get('transfer.json')
        if not info or not info.isfile() or info.size > METADATA_LIMIT:
            raise ValueError('Missing transfer metadata')
        metadata = json.load(package.extractfile(info))
        if (metadata.get('version') != 1 or metadata.get('target') != expected_target
                or metadata.get('base') not in (expected_base, None)):
            raise ValueError('Wrong image transfer identity')
        records = metadata.get('members')
        if not isinstance(records, list) or len(records) > 10000:
            raise ValueError('Invalid image manifest')
        if metadata['base'] is None:
            base_path = None
        with tarfile.open(base_path) if base_path else tarfile.open(fileobj=io.BytesIO(bytes(10240))) as base:
            cached = members(base)
            used, names, total = {'transfer.json'}, set(), 0
            with tarfile.open(destination, mode='w') as output:
                for record in records:
                    name = record['name']
                    path = PurePosixPath(name)
                    if (not name or path.is_absolute() or '..' in path.parts or str(path) != name.rstrip('/')
                            or name in names or type(record['reuse']) is not bool
                            or type(record['size']) is not int or record['size'] < 0):
                        raise ValueError('Invalid image member record')
                    names.add(name)
                    total += record['size']
                    if total > LIMIT:
                        raise ValueError('Reconstructed image exceeds limit')
                    if record['reuse']:
                        if metadata['base'] is None:
                            raise ValueError('Cannot reuse data without a base')
                        source, member = base, cached.get(name)
                    else:
                        source, member = package, package_members.get('payload/' + name)
                        used.add('payload/' + name)
                    if member is None or member.size != record['size'] or digest(source, member) != record['sha256']:
                        raise ValueError('Image entry is missing or fails SHA-256 verification')
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.size, entry.mode = member.type, member.size, member.mode
                    output.addfile(entry, source.extractfile(member) if member.isfile() else None)
            if used != set(package_members):
                raise ValueError('Unexpected transfer entries')


def export_image(base_id, target_id):
    # A backup key can only request deployed application/proxy images, not arbitrary images.
    allowed = {docker('inspect', '--format', '{{.Image}}', name) for name in ('neyro-app-1', 'neyro-caddy-1')}
    if target_id not in allowed:
        raise ValueError('Only currently deployed image IDs may be exported')
    with tempfile.TemporaryDirectory(prefix='neyro-image.', dir='/var/tmp') as temporary:
        root = Path(temporary)
        base_path = root / 'base.tar'
        try:
            save_image(base_id, base_path)
        except subprocess.SubprocessError:
            base_path, base_id = None, None
        save_image(target_id, root / 'target.tar')
        create(base_path, root / 'target.tar', sys.stdout.buffer, base_id, target_id)


def import_image(base_id, target_id):
    with tempfile.TemporaryDirectory(prefix='neyro-image.', dir='/var/tmp') as temporary:
        root = Path(temporary)
        with (root / 'package.tar.gz').open('wb') as output:
            size = 0
            while chunk := sys.stdin.buffer.read(1024**2):
                size += len(chunk)
                if size > LIMIT:
                    raise ValueError('Transfer exceeds size limit')
                output.write(chunk)
        save_image(base_id, root / 'base.tar')
        assemble(root / 'base.tar', root / 'package.tar.gz', root / 'assembled.tar', base_id, target_id)
        subprocess.run(['docker', 'image', 'load', '-i', str(root / 'assembled.tar')], timeout=180,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if docker('image', 'inspect', '--format', '{{.Id}}', target_id) != target_id:
            raise ValueError('Docker did not confirm the expected image ID')
        print('Verified exact backup image: ' + target_id + f'; transfer_bytes={(root / "package.tar.gz").stat().st_size}; archive_bytes={(root / "assembled.tar").stat().st_size}')


if __name__ == '__main__':
    os.umask(0o077)
    def interrupted(signum, frame):
        raise SystemExit('Image transfer interrupted; temporary files removed')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['export', 'import'])
    parser.add_argument('base', type=image_id)
    parser.add_argument('target', type=image_id)
    args = parser.parse_args()
    try:
        (export_image if args.mode == 'export' else import_image)(args.base, args.target)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError, subprocess.SubprocessError):
        raise SystemExit('Image transfer failed; snapshot remains unverified')
