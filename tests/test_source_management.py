import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from app.sources.manage import copy_sources, add_forward_source
from app.bot.handlers import on_forward_action


@pytest.mark.asyncio
async def test_copy_atomic_scoped_and_idempotent(store, channel):
    target = await store.insert("INSERT INTO channels(owner_id,chat_id,created_at) VALUES(1,-200,now())")
    source = await store.insert("INSERT INTO sources(channel_id,kind,ref,title,last_uid,error,enabled,created_at) VALUES(?,'tg','example','Example','old','failed',0,now())", (channel['id'],))
    results = await asyncio.gather(*(copy_sources(1, target, channel['id'], [source]) for _ in range(2)))
    assert sum(r['added'] for r in results) == 1
    assert sum(r['skipped'] for r in results) == 1
    row = await store.fetch_one('SELECT * FROM sources WHERE channel_id=?', (target,))
    assert row['last_uid'] is None and row['error'] is None and row['enabled'] == 1
    with pytest.raises(ValueError):
        await copy_sources(1, target, channel['id'], [source, 999999])
    with pytest.raises(PermissionError):
        await copy_sources(2, target, channel['id'], [source])
    with pytest.raises(ValueError):
        await copy_sources(1, target, target, [source])
    with pytest.raises(ValueError):
        await copy_sources(1, target, channel['id'], [True])


@pytest.mark.asyncio
async def test_forward_add_rechecks_owner_and_duplicate(store, channel):
    with pytest.raises(PermissionError):
        await add_forward_source(2, channel['id'], 'example', 'Example')
    assert await add_forward_source(1, channel['id'], 'example', 'Example')
    assert not await add_forward_source(1, channel['id'], 'example', 'Example')


@pytest.mark.asyncio
async def test_expired_or_foreign_forward_callback(store, owner):
    await store.set_kv('forward:1', {'token':'valid', 'expires':0})
    for data in ('src:add:valid:1', 'src:choose:foreign', 'src:post:valid'):
        callback = SimpleNamespace(data=data, from_user=SimpleNamespace(id=1), answer=AsyncMock())
        await on_forward_action(callback, AsyncMock())
        callback.answer.assert_awaited_once()
        assert callback.answer.call_args.kwargs['show_alert']


@pytest.mark.asyncio
async def test_forward_snapshot_manual_choice_consumed_once(store, owner, monkeypatch):
    from aiogram.types import Message
    from app.bot import handlers
    from aiogram import Bot
    bot = Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk')
    message = Message.model_validate({'message_id':10,'date':1700000000,'chat':{'id':1,'type':'private'},'from_user':{'id':1,'is_bot':False,'first_name':'Test'},'text':'Исходный текст','forward_origin':{'type':'channel','date':1700000000,'chat':{'id':-100123,'type':'channel','username':'example','title':'Example'},'message_id':50}}).as_(bot)
    monkeypatch.setattr(Message, 'answer', AsyncMock())
    manual = AsyncMock()
    monkeypatch.setattr(handlers, 'on_manual', manual)
    await handlers.on_forward(message, bot)
    state = await store.get_kv('forward:1')
    assert state['ref'] == 'example'
    callback = SimpleNamespace(data=f"src:post:{state['token']}",from_user=SimpleNamespace(id=1),answer=AsyncMock(),message=message)
    await handlers.on_forward_action(callback, bot)
    await handlers.on_forward_action(callback, bot)
    manual.assert_awaited_once()
    assert manual.call_args.args[0].text == 'Исходный текст'
    await bot.session.close()
