from datetime import timedelta
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app import db
from app.core import publisher, scheduler
from tests.test_pipeline_db import FakeBot, make_post


@pytest.mark.asyncio
async def test_concurrent_last_quota_only_one_send(store, channel):
    await store.execute('UPDATE users SET daily_limit=1 WHERE tg_id=1')
    posts = [await make_post(store, channel, text=f'Post {i}') for i in range(2)]
    bot = FakeBot()
    results = await asyncio.gather(*(publisher.publish_post(bot, p, channel) for p in posts), return_exceptions=True)
    assert len(bot.sent) == 1
    assert sum(isinstance(r, publisher.QuotaExceeded) for r in results) == 1
    assert await publisher.used_today(1) == 1


@pytest.mark.asyncio
async def test_timeout_never_retries_and_requires_reconciliation(store, channel):
    post = await make_post(store, channel)
    bot = FakeBot()
    bot.send_message = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(publisher.DeliveryUncertain):
        await publisher.publish_post(bot, post, channel)
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot, post, channel)
    assert bot.send_message.await_count == 1
    assert await publisher.used_today(1) == 1
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.reconcile_delivery(post['id'], channel, delivered=False)
    await store.execute("UPDATE delivery_attempts SET started_at=now()-interval '11 minutes' WHERE post_id=?", (post['id'],))
    await publisher.reconcile_delivery(post['id'], channel, delivered=False)
    assert await publisher.used_today(1) == 0
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (post['id'],)))['status'] == 'pending'


@pytest.mark.asyncio
async def test_media_success_text_failure_keeps_receipt_and_blocks_replay(store, channel):
    post = await make_post(store, channel, text='Текст ' * 400)
    await store.execute('UPDATE posts SET media=? WHERE id=?', (json.dumps([{'type':'photo','file_id':'photo'}]), post['id']))
    bot = FakeBot()
    bot.send_message = AsyncMock(side_effect=TelegramBadRequest(method=SendMessage(chat_id=1,text='x'), message='rejected'))
    with pytest.raises(publisher.DeliveryUncertain):
        await publisher.publish_post(bot, post, channel)
    fresh = await store.fetch_one('SELECT * FROM posts WHERE id=?', (post['id'],))
    attempt = await store.fetch_one('SELECT * FROM delivery_attempts WHERE post_id=?', (post['id'],))
    assert fresh['status'] == 'partial'
    assert json.loads(attempt['receipts'])[0]['message_ids'] == [1]
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.reconcile_delivery(post['id'], channel, delivered=False)
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot, post, channel)
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_db_receipt_failure_does_not_make_success_retryable(store, channel, monkeypatch):
    post = await make_post(store, channel)
    original = db.execute
    async def fail_receipt(sql, params=()):
        if 'SET receipts=' in sql:
            raise RuntimeError('DB failure after Telegram accepted')
        return await original(sql, params)
    monkeypatch.setattr(db, 'execute', fail_receipt)
    bot = FakeBot()
    with pytest.raises(publisher.DeliveryUncertain):
        await publisher.publish_post(bot, post, channel)
    assert len(bot.sent) == 1
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?',(post['id'],)))['status'] == 'partial'
    await publisher.reconcile_delivery(post['id'], channel, delivered=True)
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.reconcile_delivery(post['id'], channel, delivered=True)
    assert await publisher.used_today(1) == 1


@pytest.mark.asyncio
async def test_recover_old_attempt_but_not_live_one(store, channel):
    posts = [await make_post(store,channel,text=f'Post{i}') for i in range(2)]
    for i,p in enumerate(posts):
        await store.execute("UPDATE posts SET status='publishing' WHERE id=?",(p['id'],))
        await store.execute("INSERT INTO delivery_attempts (post_id,channel_id,owner_id,worker_id,started_at) VALUES (?,?,1,'dead',now()-?::interval)",(p['id'],channel['id'],timedelta(minutes=11 if i==0 else 1)))
    assert await publisher.recover_stale_deliveries() == 1
    rows = await store.fetch_all('SELECT status FROM posts ORDER BY id')
    assert [r['status'] for r in rows] == ['uncertain','publishing']


@pytest.mark.asyncio
async def test_deleted_channel_does_not_refund_sent_quota(store, channel):
    await publisher.publish_post(FakeBot(), await make_post(store,channel), channel)
    await store.execute('DELETE FROM channels WHERE id=?',(channel['id'],))
    assert await publisher.used_today(1) == 1


@pytest.mark.asyncio
async def test_digest_is_one_quota_unit_and_not_duplicated(store, channel, monkeypatch):
    channel.update(autopost=1,digest_enabled=1)
    await store.execute('UPDATE channels SET autopost=1,digest_enabled=1 WHERE id=?',(channel['id'],))
    for i in range(2):
        await make_post(store, channel, status='digest',text=f'News {i}')
    monkeypatch.setattr(scheduler.pipeline,'make_digest',AsyncMock(return_value='Сводка'))
    monkeypatch.setattr(scheduler.pipeline,'_factcheck',AsyncMock(return_value={'ok':True}))
    bot = FakeBot()
    await asyncio.gather(scheduler.run_digest(bot,channel),scheduler.run_digest(bot,channel))
    assert len(bot.sent) == 1
    assert await publisher.used_today(1) == 1
    statuses = await store.fetch_all('SELECT status FROM posts ORDER BY id')
    assert [r['status'] for r in statuses] == ['digest_item','digest_item','published']


@pytest.mark.asyncio
async def test_final_db_transaction_failure_keeps_attempt_for_recovery(store, channel, monkeypatch):
    from contextlib import asynccontextmanager
    post = await make_post(store, channel)
    real_pool = await db.connect()
    class Conn:
        def __init__(self, conn): self.conn = conn
        def __getattr__(self, name): return getattr(self.conn, name)
        async def execute(self, sql, *args):
            if "SET status='sent'" in sql:
                raise RuntimeError('commit bookkeeping failed')
            return await self.conn.execute(sql, *args)
    class Pool:
        @asynccontextmanager
        async def acquire(self):
            async with real_pool.acquire() as conn:
                yield Conn(conn)
        def __getattr__(self, name): return getattr(real_pool, name)
    original = db.connect
    monkeypatch.setattr(db, 'connect', AsyncMock(return_value=Pool()))
    bot = FakeBot()
    with pytest.raises(RuntimeError, match='bookkeeping'):
        await publisher.publish_post(bot, post, channel)
    monkeypatch.setattr(db, 'connect', original)
    assert len(bot.sent) == 1
    saved = await store.fetch_one('SELECT status FROM posts WHERE id=?', (post['id'],))
    assert saved['status'] == 'publishing'
    attempt = await store.fetch_one('SELECT * FROM delivery_attempts WHERE post_id=?',(post['id'],))
    assert attempt['status'] == 'sending' and json.loads(attempt['receipts'])
    await store.execute("UPDATE delivery_attempts SET started_at=now()-interval '11 minutes'")
    await publisher.recover_stale_deliveries()
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot, post, channel)
    await publisher.reconcile_delivery(post['id'],channel,delivered=True)
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_legacy_publishing_is_quarantined(store, channel):
    post = await make_post(store,channel)
    await store.execute("UPDATE posts SET status='publishing',created_at=now()-interval '1 day' WHERE id=?",(post['id'],))
    assert await publisher.recover_stale_deliveries() == 1
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?',(post['id'],)))['status'] == 'uncertain'
    assert await publisher.recover_stale_deliveries() == 0


@pytest.mark.asyncio
async def test_history_exposes_receipts_only_to_owner(store, channel):
    from app.api.routes import channel_history
    from fastapi import HTTPException
    post=await make_post(store,channel)
    await publisher.publish_post(FakeBot(),post,channel)
    owner=await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    history=await channel_history(channel['id'],owner)
    assert history[0]['delivery_receipts'][0]['message_ids']==[1]
    with pytest.raises(HTTPException):
        await channel_history(channel['id'],{**owner,'tg_id':999})
