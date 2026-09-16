import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai.metrics import ApiMetrics
from app.ai import gemini


def test_windows_and_bounded_memory():
    now = [0]
    metrics = ApiMetrics(lambda: now[0])
    for status in (200, 429, 503, 'transport', 'interrupted'):
        metrics.record(status, 2)
    metrics.fallback()
    data = metrics.snapshot()['windows']['15']
    assert (data['requests'], data['errors'], data['quota'], data['average_ms'], data['fallbacks']) == (5, 4, 1, 2000, 1)
    now[0] = 15*60
    assert metrics.snapshot()['windows']['15']['requests'] == 0
    assert metrics.snapshot()['windows']['60']['requests'] == 5
    for minute in range(60, 10000):
        now[0] = minute*60
        metrics.record(200, 1)
    assert len(metrics.buckets) == 61
    assert len(metrics.snapshot()['series']) == 15
    assert metrics.snapshot()['windows']['60']['requests'] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 429, 503, 'timeout'])
async def test_actual_http_attempts(monkeypatch, status):
    metrics = ApiMetrics()
    monkeypatch.setattr(gemini, 'metrics', metrics)
    monkeypatch.setattr(gemini, 'ai_slots', asyncio.Semaphore(1))
    response = httpx.Response(status if isinstance(status, int) else 200,
                              json={'candidates':[{'content':{'parts':[{'text':'ok'}]}}]})
    post = AsyncMock(return_value=response)
    if status == 'timeout':
        post.side_effect = httpx.ReadTimeout('timeout')
    client = type('Client', (), {'post': post})()
    try:
        await gemini._call(client, 'test-model', 'private-key', 'private-prompt', None, 0, False)
    except (gemini.AIError, httpx.ReadTimeout):
        assert status != 200
    data = metrics.snapshot()
    assert data['active'] == data['waiting'] == 0
    assert data['windows']['15']['requests'] == 1
    assert data['windows']['15']['errors'] == int(status != 200)
    assert 'private' not in str(data)


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', [True, False])
async def test_cancellation_releases_counters(monkeypatch, blocked):
    metrics = ApiMetrics()
    monkeypatch.setattr(gemini, 'metrics', metrics)
    monkeypatch.setattr(gemini, 'ai_slots', asyncio.Semaphore(0 if blocked else 1))
    async def post(*args, **kwargs):
        await asyncio.Event().wait()
    client = type('Client', (), {'post': post})()
    task = asyncio.create_task(gemini._call(client, 'model', 'secret', 'prompt', None, 0, False))
    await asyncio.sleep(0)
    assert (metrics.waiting, metrics.active) == ((1,0) if blocked else (0,1))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert metrics.waiting == metrics.active == 0
    assert metrics.snapshot()['windows']['15']['requests'] == int(not blocked)
