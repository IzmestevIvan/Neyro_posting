import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ha_alerts_test', 'scripts/resilience/alerts.py')
alerts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alerts)
with patch.dict(sys.modules, {'alerts': alerts}):
    spec = importlib.util.spec_from_file_location('ha_monitor_test', 'scripts/ha/monitor.py')
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)


def test_backup_age_rejects_missing_stale_and_future(tmp_path):
    path = tmp_path / 'last-success'
    assert not monitor.fresh_stamp(path, 1800)
    path.write_text('19700101T000000Z')
    assert monitor.fresh_stamp(path, 1800)
    assert not monitor.fresh_stamp(path, 1801)
    assert not monitor.fresh_stamp(path, -1)
    path.write_text('broken')
    assert not monitor.fresh_stamp(path, 0)


def test_outage_and_bad_response_fail_closed():
    with patch.object(monitor.urllib.request, 'urlopen', side_effect=TimeoutError):
        assert not monitor.primary_healthy()
