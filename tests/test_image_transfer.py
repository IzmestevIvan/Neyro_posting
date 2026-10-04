import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('image_transfer', Path(__file__).resolve().parents[1] / 'scripts/resilience/image-transfer.py')
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)
BASE = 'sha256:' + 'a'*64
TARGET = 'sha256:' + 'b'*64


def archive(path, entries, mode='w'):
    with tarfile.open(path, mode) as output:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.type, member.size = kind, len(data)
            output.addfile(member, io.BytesIO(data))


class ImageTransfer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old, self.new, self.package, self.result = [self.root / name for name in ('old.tar','new.tar','delta.tar.gz','result.tar')]
        common = ('blobs/sha256/common', b'a'*100000, tarfile.REGTYPE)
        archive(self.old, [common, ('manifest.json', b'old', tarfile.REGTYPE)])
        archive(self.new, [common, ('manifest.json', b'new', tarfile.REGTYPE), ('blobs/sha256/new', b'new layer', tarfile.REGTYPE)])
        with self.package.open('wb') as output:
            transfer.create(self.old, self.new, output, BASE, TARGET)

    def read(self, path):
        with tarfile.open(path) as source:
            return {member.name: source.extractfile(member).read() for member in source if member.isfile()}

    def test_delta_reuses_common_data_and_reconstructs_exact_contents(self):
        payload = self.read(self.package)
        metadata = json.loads(payload['transfer.json'])
        self.assertTrue(metadata['members'][0]['reuse'])
        self.assertNotIn('payload/blobs/sha256/common', payload)
        transfer.assemble(self.old, self.package, self.result, BASE, TARGET)
        self.assertEqual(self.read(self.new), self.read(self.result))

    def test_absent_source_base_falls_back_to_compressed_full_image(self):
        with self.package.open('wb') as output:
            transfer.create(None, self.new, output, None, TARGET)
        transfer.assemble(self.old, self.package, self.result, BASE, TARGET)
        self.assertEqual(self.read(self.new), self.read(self.result))

    def test_wrong_expected_target_or_base_refused(self):
        for base, target in [(TARGET,TARGET), (BASE,BASE)]:
            with self.assertRaises(ValueError):
                transfer.assemble(self.old, self.package, self.result, base, target)

    def test_corrupt_cached_content_rejected(self):
        archive(self.old, [('blobs/sha256/common', b'b'*100000, tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            transfer.assemble(self.old, self.package, self.result, BASE, TARGET)

    def test_corrupt_delta_or_unexpected_entries_rejected(self):
        original = self.read(self.package)
        for entries in [{**original, 'payload/blobs/sha256/new':b'bad layer'}, {**original,'extra':b'bad'}]:
            archive(self.package, [(name, data, tarfile.REGTYPE) for name, data in entries.items()], mode='w:gz')
            with self.assertRaises(ValueError):
                transfer.assemble(self.old, self.package, self.result, BASE, TARGET)

    def test_unsafe_names_links_duplicates_and_oversized_metadata_rejected(self):
        for name, kind in [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('a/./b',tarfile.REGTYPE),('link',tarfile.SYMTYPE),('link',tarfile.LNKTYPE)]:
            archive(self.new,[(name,b'',kind)])
            with self.package.open('wb') as output, self.assertRaises(ValueError):
                transfer.create(self.old, self.new, output, BASE, TARGET)
        archive(self.new,[('duplicate',b'a',tarfile.REGTYPE),('duplicate',b'b',tarfile.REGTYPE)])
        with self.package.open('wb') as output, self.assertRaises(ValueError):
            transfer.create(self.old, self.new, output, BASE, TARGET)
        archive(self.package,[('transfer.json',b' '* (transfer.METADATA_LIMIT+1),tarfile.REGTYPE)],mode='w:gz')
        with self.assertRaises(ValueError):
            transfer.assemble(self.old,self.package,self.result,BASE,TARGET)

    def test_missing_payload_rejected(self):
        data = self.read(self.package)
        del data['payload/manifest.json']
        archive(self.package,[(name,value,tarfile.REGTYPE) for name,value in data.items()],mode='w:gz')
        with self.assertRaises(ValueError):
            transfer.assemble(self.old,self.package,self.result,BASE,TARGET)

    def test_image_id_rejects_shell_and_paths(self):
        self.assertEqual(transfer.image_id(BASE),BASE)
        for value in ['latest','../../secret',BASE+';id','sha256:short']:
            with self.assertRaises(ValueError):
                transfer.image_id(value)


if __name__ == '__main__':
    unittest.main()
