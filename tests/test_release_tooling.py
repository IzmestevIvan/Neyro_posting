"""Exercise release trust boundaries and failure recovery without SSH/Docker."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load('release_tool', 'scripts/deploy/neyro-release.py')
dispatch = load('dispatch_tool', 'scripts/deploy/neyro-deploy-dispatch.py')
bundle = load('bundle_tool', 'scripts/deploy/bundle.py')
SHA = 'a' * 40


def make_archive(path, files, mutate=None):
    metadata = {'commit': SHA, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    members = [(name, data, tarfile.REGTYPE) for name, data in files.items()]
    members.append(('_release.json', json.dumps(metadata).encode(), tarfile.REGTYPE))
    if mutate:
        mutate(members)
    with tarfile.open(path, 'w:gz') as archive:
        for name, data, kind in members:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.type = kind
            archive.addfile(member, io.BytesIO(data))


def fixture_files():
    return {'Dockerfile': b'FROM python:3.12-slim\n', 'requirements.lock': b'fastapi==0.141.1\n',
            'app/main.py': b'new app', 'app/db.py': b'same schema', 'app/billing/schema.py': b'same billing schema'}


class BundleSecurity(unittest.TestCase):
    def test_upload_timeout_is_bounded_and_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state/'uploads').mkdir()
            (state/'status').mkdir()
            class SlowInput:
                def read(self, _size):
                    dispatch.upload_timeout(None, None)
            with patch.object(dispatch, 'STATE', state), \
                 patch.dict(os.environ, {'SSH_ORIGINAL_COMMAND': f'upload {SHA} '+ 'b'*64}), \
                 patch.object(dispatch.signal, 'signal'), patch.object(dispatch.signal, 'alarm') as alarm, \
                 patch.object(dispatch.sys, 'stdin', SimpleNamespace(buffer=SlowInput())):
                with self.assertRaises(TimeoutError):
                    dispatch.main()
                alarm.assert_called_once_with(600)
            self.assertEqual(list((state/'uploads').iterdir()), [])

    def test_valid_bundle_and_commit_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'bundle.tar.gz'
            files = fixture_files()
            make_archive(archive, files)
            self.assertEqual(release.validate_bundle(archive, SHA), files)
            with self.assertRaises(ValueError):
                release.validate_bundle(archive, 'b' * 40)

    def test_rejects_traversal_links_duplicate_aliases_secrets_and_tampering(self):
        attacks = [
            ('app/../../escape', b'x', tarfile.REGTYPE),
            ('/app/absolute', b'x', tarfile.REGTYPE),
            ('app/./alias', b'x', tarfile.REGTYPE),
            ('app/.env', b'x', tarfile.REGTYPE),
            ('.app.env', b'x', tarfile.REGTYPE),
            ('app/link', b'', tarfile.SYMTYPE),
            ('app/hardlink', b'', tarfile.LNKTYPE),
            ('app/main.py', b'tampered duplicate', tarfile.REGTYPE),
            ('unexpected', b'x', tarfile.REGTYPE),
        ]
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'bundle.tar.gz'
            for attack in attacks:
                with self.subTest(name=attack[0]):
                    make_archive(archive, fixture_files(), lambda members: members.append(attack))
                    with self.assertRaises(ValueError):
                        release.validate_bundle(archive, SHA)
            def corrupt(members):
                members[0] = ('Dockerfile', b'corrupt', tarfile.REGTYPE)
            make_archive(archive, fixture_files(), corrupt)
            with self.assertRaisesRegex(ValueError, 'digest'):
                release.validate_bundle(archive, SHA)

    def test_dispatch_rejects_shell_commands_and_ambiguous_inputs(self):
        for value in ['', 'bash', 'status ../../etc/passwd', f'deploy {SHA};id',
                      f'deploy {SHA} extra', f'upload {SHA} bad', f'status {SHA.upper()}']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                dispatch.parse_command(value)
        self.assertEqual(dispatch.parse_command(f'status {SHA}'), ('status', SHA, None))
        self.assertEqual(dispatch.parse_command(f'upload {SHA} '+ 'b'*64), ('upload', SHA, 'b'*64))

    def test_bundle_uses_commit_instead_of_dirty_working_tree(self):
        files = fixture_files()
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'source.tar.gz'
            make_archive(src, {**files, '.env': b'private', 'data/private': b'private'})
            # Git archive is uncompressed; only its source content matters here.
            with tarfile.open(src) as source, io.BytesIO() as raw:
                with tarfile.open(fileobj=raw, mode='w') as target:
                    for member in source:
                        target.addfile(member, source.extractfile(member))
                committed = raw.getvalue()
            out = Path(directory) / 'release.tar.gz'
            with patch.object(bundle.subprocess, 'check_output', return_value=committed):
                bundle.build_bundle(SHA, out)
            self.assertEqual(release.validate_bundle(out, SHA), files)

    def test_server_checks_main_ci_and_committed_source(self):
        files = fixture_files()
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as archive:
            for name, data in files.items():
                item = tarfile.TarInfo('Neyro_posting-'+SHA+'/'+name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        responses = [{'object': {'sha': SHA}}, {'workflow_runs': [{'head_sha':SHA,'conclusion':'success'}]}]
        with patch.object(release, 'github_json', side_effect=responses), patch.object(release.urllib.request, 'urlopen', return_value=io.BytesIO(raw.getvalue())):
            release.verify_approved_source(SHA, files)
        with patch.object(release, 'github_json', side_effect=responses), patch.object(release.urllib.request, 'urlopen', return_value=io.BytesIO(raw.getvalue())):
            with self.assertRaisesRegex(ValueError, 'differs'):
                release.verify_approved_source(SHA, {**files,'Dockerfile':b'bad'})
        for metadata in [[{'object':{'sha':'b'*40}}], [{'object':{'sha':SHA}},{'workflow_runs':[]}]]:
            with patch.object(release, 'github_json', side_effect=metadata), self.assertRaises(ValueError):
                release.verify_approved_source(SHA, files)


class ReleaseRecovery(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base/'production'
        self.state = self.base/'state'
        self.locks = self.base/'locks'
        for path in [self.root, self.root/'backups', self.state/'uploads', self.state/'status',self.locks]:
            path.mkdir(parents=True, exist_ok=True)
        for name, data in fixture_files().items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'old app' if name=='app/main.py' else data)
        self.env = self.root/'.app.env'
        self.env.write_text('BILLING_ENABLED=0\n')
        make_archive(self.state/'uploads'/f'{SHA}.tar.gz',fixture_files())
        for attribute,value in [('ROOT',self.root),('STATE',self.state),('LOCKS',self.locks),('RESERVE_MARKER',self.base/'reserve'),('OPERATIONS',self.base/'operations')]:
            patcher=patch.object(release,attribute,value);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(release,'verify_approved_source');patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(release,'capture',side_effect=lambda args: 'sha256:old-support' if args[-1]=='neyro-support-1' else 'sha256:old-app')
        patcher.start();self.addCleanup(patcher.stop)
        self.commands=[]

    def fake_run(self,args,**kwargs):
        self.commands.append(args)
        if 'stdout' in kwargs:
            kwargs['stdout'].write(b'encrypted fixture snapshot')

    def test_success_keeps_environment_and_does_not_restart_database_proxy(self):
        with patch.object(release,'run',side_effect=self.fake_run), patch.object(release,'verify_health'):
            release.deploy(SHA)
        self.assertEqual((self.root/'app/main.py').read_bytes(),b'new app')
        self.assertEqual(self.env.read_text(),'BILLING_ENABLED=0\n')
        self.assertEqual(json.loads((self.state/'status'/f'{SHA}.json').read_text())['status'],'success')
        commands=[args for args in self.commands if 'up' in args]
        self.assertEqual(len(commands),1)
        self.assertEqual(commands[0][-2:],['app','support'])
        self.assertIn('--no-deps',commands[0])

    def test_unhealthy_release_restores_source_and_each_worker_image(self):
        with patch.object(release,'run',side_effect=self.fake_run), patch.object(release,'verify_health',side_effect=[RuntimeError('bad release'),None]):
            with self.assertRaisesRegex(RuntimeError,'bad release'):
                release.deploy(SHA)
        self.assertEqual((self.root/'app/main.py').read_bytes(),b'old app')
        status=json.loads((self.state/'status'/f'{SHA}.json').read_text())
        self.assertEqual(status['phase'],'rolled_back')
        override=json.loads((Path(status['backup'])/'rollback.json').read_text())
        self.assertEqual(override['services']['support']['image'],'sha256:old-support')
        self.assertEqual(override['services']['app']['image'],'sha256:old-app')
        self.assertEqual(self.env.read_text(),'BILLING_ENABLED=0\n')

    def test_build_failure_never_activates(self):
        def fail(args,**kwargs):
            self.fake_run(args,**kwargs)
            if args[:2]==['docker','build']:
                raise subprocess.CalledProcessError(1,args)
        with patch.object(release,'run',side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                release.deploy(SHA)
        self.assertEqual((self.root/'app/main.py').read_bytes(),b'old app')
        self.assertFalse(any('up' in args for args in self.commands))

    def test_maintenance_and_fence_refuse_release_before_backup_or_build(self):
        release.OPERATIONS.mkdir()
        for marker in ('maintenance', 'FENCED'):
            path = release.OPERATIONS / marker
            path.touch()
            with patch.object(release, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'guard'):
                    release.deploy(SHA)
                run.assert_not_called()
            path.unlink()

    def test_schema_change_or_reserve_stops_before_backup_or_build(self):
        (self.root/'app/db.py').write_bytes(b'other schema')
        with patch.object(release,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'Schema'):
                release.deploy(SHA)
            run.assert_not_called()
        (self.root/'app/db.py').write_bytes(fixture_files()['app/db.py'])
        release.RESERVE_MARKER.mkdir()
        with patch.object(release,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'reserve'):
                release.deploy(SHA)
            run.assert_not_called()

    def test_promoted_reserve_allows_release_only_with_primary_role(self):
        release.RESERVE_MARKER.mkdir()
        (release.RESERVE_MARKER / 'ACTIVE').touch()
        release.OPERATIONS.mkdir()
        config = release.OPERATIONS / 'config.json'
        config.write_text(json.dumps({'role': 'reserve'}))
        with patch.object(release, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'reserve'):
                release.deploy(SHA)
            run.assert_not_called()
        config.write_text(json.dumps({'role': 'primary'}))
        with patch.object(release, 'run', side_effect=self.fake_run), patch.object(release, 'verify_health'):
            release.deploy(SHA)
        self.assertEqual(json.loads((self.state / 'status' / f'{SHA}.json').read_text())['status'], 'success')


if __name__ == '__main__':
    unittest.main()
