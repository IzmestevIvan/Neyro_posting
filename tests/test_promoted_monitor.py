import json
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/resilience/check-health.py'

class PromotedMonitorTests(unittest.TestCase):
    def check_role(self, active):
        reads=[]
        notify=Mock()
        def read(path, *args, **kwargs):
            reads.append(str(path))
            if str(path).endswith('health-url'): return 'https://example.test/healthz'
            raise FileNotFoundError
        response=Mock(status=200)
        response.read.return_value=b'{"ok":true}'
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        with patch.object(Path,'exists',return_value=active), patch.object(Path,'read_text',read), patch.object(Path,'write_text'), patch.object(Path,'replace'), patch('os.chmod'), patch('urllib.request.urlopen',return_value=response), patch.dict('sys.modules',{'neyro_alerts':SimpleNamespace(notify=notify)}):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(SCRIPT),run_name='__main__')
        self.assertEqual(result.exception.code,1)
        checks=notify.call_args.args[0]
        self.assertTrue(checks['primary_healthy'])
        self.assertFalse(checks['backup_fresh'])
        return reads,notify

    def test_active_never_counts_old_primary_backup(self):
        reads,notify=self.check_role(True)
        self.assertIn('/var/lib/neyro-reserve/active-offsite-last-success',reads)
        self.assertNotIn('/var/lib/neyro-reserve/last-success',reads)
        self.assertIn('/etc/neyro-reserve/active-health-url',reads)
        self.assertEqual(notify.call_args.kwargs['source'],'active')

    def test_standby_preserves_original_checks(self):
        reads,notify=self.check_role(False)
        self.assertIn('/var/lib/neyro-reserve/last-success',reads)
        self.assertIn('/etc/neyro-reserve/health-url',reads)
        self.assertIsNone(notify.call_args.kwargs['source'])
