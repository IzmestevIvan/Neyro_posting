from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.ai import gemini, key_pool
from app.api import routes, auth


@pytest.mark.asyncio
async def test_pool_switches_and_cools_failed_key(monkeypatch):
    monkeypatch.setattr(gemini, 'GEMINI_API_KEY', '')
    monkeypatch.setattr(key_pool, 'candidates', AsyncMock(return_value=[{'id':1,'secret':'one'}, {'id':2,'secret':'two'}]))
    monkeypatch.setattr(key_pool, 'claim', AsyncMock(return_value=True))
    fail, success = AsyncMock(), AsyncMock()
    monkeypatch.setattr(key_pool, 'failed', fail)
    monkeypatch.setattr(key_pool, 'succeeded', success)
    call = AsyncMock(side_effect=[gemini.AIError('HTTP 403'), 'done'])
    monkeypatch.setattr(gemini, '_call', call)
    assert await gemini.generate('text', allow_fallback=False) == 'done'
    assert [c.args[2] for c in call.call_args_list] == ['one','two']
    fail.assert_awaited_once_with(1, 'HTTP 403')
    success.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_key_api_denies_regular_user_before_database(monkeypatch):
    monkeypatch.setattr(auth, 'ADMIN_IDS', set())
    read = AsyncMock()
    monkeypatch.setattr(key_pool, 'overview', read)
    with pytest.raises(HTTPException) as error:
        await routes.list_api_keys({'tg_id':1, 'is_admin':0})
    assert error.value.status_code == 403
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_key_query_never_selects_secrets(monkeypatch):
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(key_pool.db, 'fetch_all', read)
    await key_pool.overview()
    assert 'secret' not in read.call_args.args[0] and '*' not in read.call_args.args[0]


@pytest.mark.asyncio
async def test_invalid_keys_rejected_before_database(monkeypatch):
    connect = AsyncMock()
    monkeypatch.setattr(key_pool.db, 'connect', connect)
    with pytest.raises(ValueError):
        await key_pool.add_many('not a key')
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_pool_database_lifecycle(store):
    secret = 'AIza' + 'x' * 35
    assert await key_pool.add_many(secret + '\n' + secret) == 1
    assert await key_pool.add_many(secret) == 0
    public = await key_pool.overview()
    assert 'secret' not in public[0] and secret not in str(public)
    key_id = public[0]['id']
    assert await key_pool.claim(key_id)
    await key_pool.failed(key_id, 'HTTP 429')
    assert await key_pool.candidates() == []
    assert not await key_pool.claim(key_id)
    await key_pool.succeeded(key_id)
    assert len(await key_pool.candidates()) == 1
    await routes.change_api_key(key_id, {'enabled': False}, {'tg_id':1, 'is_admin':1})
    assert await key_pool.candidates() == []
    await routes.remove_api_key(key_id, {'tg_id':1, 'is_admin':1})
    assert await key_pool.overview() == []
