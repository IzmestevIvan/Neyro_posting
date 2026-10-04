"""The live load generator must fail closed before sending any load."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('load_http', Path(__file__).resolve().parents[1] / 'scripts/operations/load_http.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.asyncio
@pytest.mark.parametrize('row', [
    {'health_ok': False, 'db_healthy': True, 'available_mib': 1000},
    {'health_ok': True, 'db_healthy': False, 'available_mib': 1000},
    {'health_ok': True, 'db_healthy': True, 'available_mib': 128},
    {},
])
async def test_unhealthy_preflight_sends_no_requests_and_closes_observers(tmp_path, monkeypatch, row):
    processes = []

    class Process:
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data((json.dumps(row)+'\n').encode())
            self.returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def spawn(*args, **kwargs):
        process = Process()
        processes.append(process)
        return process

    async def no_request(*args, **kwargs):
        pytest.fail('An unhealthy observer must prevent live HTTP load')

    original_sleep = asyncio.sleep

    async def short_sleep(seconds):
        await original_sleep(0)

    monkeypatch.setattr(probe.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(probe.asyncio, 'sleep', short_sleep)
    monkeypatch.setattr(probe.httpx.AsyncClient, 'get', no_request)
    report = await probe.run_trial(tmp_path/'report.json', seconds=5)
    assert report['stop_reason']
    assert report['stages'] == []
    assert report['channels_changed'] is False
    assert all(process.returncode == 0 for process in processes)
    assert json.loads((tmp_path/'report.json').read_text())['stop_reason'] == report['stop_reason']
