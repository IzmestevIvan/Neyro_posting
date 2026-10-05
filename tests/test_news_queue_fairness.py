"""Fresh independent stories must survive bursts, retries and later publications."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.api.routes import channel_stats
from app.core import publisher, scheduler
from tests.test_pipeline_db import FakeBot, make_post


@pytest.mark.asyncio
async def test_burst_drains_oldest_first_without_losing_other_stories(store, channel):
    await store.execute('UPDATE users SET daily_limit=100 WHERE tg_id=1')
    await store.execute('UPDATE channels SET autopost=1 WHERE id=?', (channel['id'],))
    ids = []
    for n in range(5):
        post = await make_post(store, channel, text=f'Independent story {n}')
        ids.append(post['id'])
        await store.execute('UPDATE posts SET created_at=? WHERE id=?',
                            (store.utcnow() - timedelta(minutes=10-n), post['id']))
    bot = FakeBot()
    for _ in ids:
        await scheduler.publish_due(bot)
    assert [p['text'] for p in bot.sent] == [f'Independent story {n}' for n in range(5)]
    assert {r['status'] for r in await store.fetch_all('SELECT status FROM posts')} == {'published'}
    assert len(await store.fetch_all("SELECT id FROM delivery_attempts WHERE status='sent'")) == 5


@pytest.mark.asyncio
async def test_fresh_retry_survives_newer_unrelated_publication(store, channel):
    older = await make_post(store, channel, status='new', text='Earlier independent story')
    await store.execute("UPDATE posts SET created_at=now()-interval '20 minutes',"
                        "reason='фактчек недоступен',publish_at=now()+interval '5 minutes' WHERE id=?", (older['id'],))
    newer = await make_post(store, channel, text='Newer independent story')
    bot = FakeBot()
    await publisher.publish_post(bot, newer, channel)
    assert await scheduler.expire_news() == 0
    saved = await store.fetch_one('SELECT * FROM posts WHERE id=?', (older['id'],))
    assert saved['status'] == 'new' and saved['reason'] == 'фактчек недоступен'
    # Model recovered and checked the earlier story while it is still fresh.
    await store.execute("UPDATE posts SET status='approved',publish_at=now(),reason=NULL WHERE id=?", (older['id'],))
    await publisher.publish_post(bot, saved, channel)
    assert len(bot.sent) == 2
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot, saved, channel)
    assert len(bot.sent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('loop', [False, True])
async def test_processing_oldest_due_first_but_deferred_item_does_not_block(store, channel, monkeypatch, loop):
    posts = [await make_post(store, channel, status='new', text=f'Story {n}') for n in range(3)]
    for n, post in enumerate(posts):
        await store.execute('UPDATE posts SET created_at=? WHERE id=?',
                            (store.utcnow()-timedelta(minutes=10-n),post['id']))
    await store.execute("UPDATE posts SET publish_at=now()+interval '15 minutes' WHERE id=?", (posts[0]['id'],))
    processed = []
    done = asyncio.Event()
    async def process(bot, post, current):
        processed.append(post['id'])
        await store.execute("UPDATE posts SET status='pending' WHERE id=?", (post['id'],))
        if len(processed) == 2:
            done.set()
    monkeypatch.setattr(scheduler, 'process_post', process)
    if loop:
        task = asyncio.create_task(scheduler.process_loop(FakeBot()))
        try:
            await asyncio.wait_for(done.wait(), 5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        await scheduler.process_round(FakeBot())
        await scheduler.process_round(FakeBot())
    assert processed == [posts[1]['id'], posts[2]['id']]
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (posts[0]['id'],)))['status'] == 'new'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['uncertain', 'partial', 'publishing'])
async def test_delivery_block_cannot_be_bypassed_by_fresh_or_manual_send(store, channel, monkeypatch, status):
    blocked = await make_post(store, channel, status=status, text='Previous delivery')
    post = await make_post(store, channel, text='Another story')
    fetch = AsyncMock()
    monkeypatch.setattr(scheduler.telegram_web, 'fetch', fetch)
    bot = FakeBot()
    with pytest.raises(ValueError, match='сверки'):
        await scheduler.publish_fresh_once(bot, channel)
    fetch.assert_not_called()
    with pytest.raises(publisher.AlreadyPublished, match='сверки'):
        await publisher.publish_post(bot, post, channel)
    assert not bot.sent
    assert not await store.fetch_all('SELECT * FROM delivery_attempts')
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (blocked['id'],)))['status'] == status
    if status != 'publishing':
        user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
        stats = await channel_stats(channel['id'], user)
        assert stats['waiting']['delivery_review'] == 1
        assert any('сверьте' in text for text in stats['blockers'])


@pytest.mark.asyncio
async def test_history_keeps_old_delivery_block_visible(store, channel):
    from app.api.routes import channel_history
    blocked = await make_post(store, channel, status='uncertain', text='Needs manual verification')
    for n in range(55):
        await make_post(store, channel, status='filtered', text=f'Recent filtered item {n}')
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    rows = await channel_history(channel['id'], user)
    assert len(rows) == 50
    assert rows[0]['id'] == blocked['id']
    assert rows[0]['status'] == 'uncertain'


@pytest.mark.asyncio
async def test_pause_during_ai_does_not_expire_fresh_story(store, channel, monkeypatch):
    from app.ai.pipeline import Result
    post = await make_post(store, channel, status='new', text='Fresh independent story')
    async def process(*args, **kwargs):
        await store.execute('UPDATE channels SET paused=1 WHERE id=?', (channel['id'],))
        return Result(True, text='Checked result')
    monkeypatch.setattr(scheduler.pipeline, 'process', process)
    bot = FakeBot()
    await scheduler.process_post(bot, post, channel)
    saved = await store.fetch_one('SELECT * FROM posts WHERE id=?', (post['id'],))
    assert saved['status'] == 'new'
    assert not bot.sent
    # Resuming must still perform the checks, not publish the discarded AI result.
    await store.execute('UPDATE channels SET paused=0 WHERE id=?', (channel['id'],))
    check = AsyncMock(return_value=Result(True, text='Rechecked result'))
    monkeypatch.setattr(scheduler.pipeline, 'process', check)
    await scheduler.process_post(bot, saved, channel)
    check.assert_awaited_once()
    saved = await store.fetch_one('SELECT * FROM posts WHERE id=?', (post['id'],))
    assert saved['status'] == 'pending' and saved['text_out'] == 'Rechecked result'
