from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai import gemini


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(gemini, 'GEMINI_API_KEY', 'shared-test-key')
    monkeypatch.setattr(gemini.key_pool, 'candidates', AsyncMock(return_value=[]))
    monkeypatch.setattr(gemini, 'GEMINI_MODEL_MAIN', 'main-model')
    monkeypatch.setattr(gemini, 'GEMINI_MODEL_FALLBACK', 'backup-model')
    monkeypatch.setattr(gemini, 'GEMINI_MODEL_VERIFY', 'verify-model')
    monkeypatch.setattr(gemini, 'cooldown_left', AsyncMock(return_value=0))
    monkeypatch.setattr(gemini, '_start_cooldown', AsyncMock())
    monkeypatch.setattr(gemini.asyncio, 'sleep', AsyncMock())
    call = AsyncMock()
    monkeypatch.setattr(gemini, '_call', call)
    return call


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 401, 403, 429])
async def test_bad_customer_key_switches_to_service(provider, status):
    provider.side_effect = [gemini.AIError(f'HTTP {status}'), 'ready']
    assert await gemini.generate('news', api_key='customer-key') == 'ready'
    assert [c.args[2] for c in provider.call_args_list] == ['customer-key', 'shared-test-key']
    gemini._start_cooldown.assert_not_awaited()


@pytest.mark.asyncio
async def test_customer_success_does_not_use_service(provider):
    provider.return_value = 'ready'
    assert await gemini.generate('news', api_key='customer-key') == 'ready'
    provider.assert_awaited_once()
    assert provider.call_args.args[2] == 'customer-key'


@pytest.mark.asyncio
async def test_network_failure_falls_back_without_model_switch(provider):
    provider.side_effect = [httpx.ConnectError('offline'), 'ready']
    assert await gemini.generate('news', api_key='customer-key', allow_fallback=False) == 'ready'
    assert [c.args[2] for c in provider.call_args_list] == ['customer-key', 'shared-test-key']


@pytest.mark.asyncio
async def test_shared_failure_stops_and_does_not_loop(provider):
    provider.side_effect = gemini.AIError('HTTP 429')
    with pytest.raises(gemini.AIError):
        await gemini.generate('news', api_key='customer-key')
    assert provider.await_count <= 4


@pytest.mark.asyncio
async def test_verifier_model_is_last_resort_for_generation(provider):
    provider.side_effect = [gemini.AIError('HTTP 429'), gemini.AIError('HTTP 503'), 'ready']
    assert await gemini.generate('news') == 'ready'
    assert provider.call_args.args[1] == gemini.GEMINI_MODEL_VERIFY


@pytest.mark.asyncio
async def test_no_duplicate_shared_key(provider):
    provider.side_effect = gemini.AIError('HTTP 503')
    with pytest.raises(gemini.AIError):
        await gemini.generate('news', api_key='shared-test-key', allow_fallback=False)
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_switch_for_content_refusal(provider):
    provider.side_effect = gemini.AIError('SAFETY')
    with pytest.raises(gemini.AIError):
        await gemini.generate('news', api_key='customer-key')
    assert all(c.args[2] == 'customer-key' for c in provider.call_args_list)
