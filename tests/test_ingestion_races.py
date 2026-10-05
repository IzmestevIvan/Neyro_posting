from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app.ai import pipeline
from app.core import scheduler
from app.sources.telegram_web import RawItem
from tests.test_pipeline_db import make_post, FakeBot

NEWS = 'В городе открыли новую библиотеку. Посетители смогут бесплатно читать книги в зале.'


@pytest.mark.asyncio
@pytest.mark.parametrize('setting', ['autopost', 'digest_enabled'])
async def test_disabling_automation_keeps_existing_digest_items_accessible(store, channel, setting):
    from app.api.routes import update_channel, channel_feed
    await store.execute('UPDATE channels SET autopost=1,digest_enabled=1 WHERE id=?', (channel['id'],))
    post = await make_post(store, channel, status='digest', text=NEWS)
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    await update_channel(channel['id'], {setting: False}, user)
    rows = await channel_feed(channel['id'], user)
    assert [row['id'] for row in rows] == [post['id']]


async def source_row(store, channel, cursor='old'):
    sid = await store.insert("INSERT INTO sources(channel_id,kind,ref,last_uid,created_at) VALUES(?,'tg','news',?,now())",
                             (channel['id'], cursor))
    return await store.fetch_one('SELECT * FROM sources WHERE id=?', (sid,))


@pytest.mark.asyncio
@pytest.mark.parametrize('cursor', [None, 'old'])
async def test_future_item_cannot_hide_current_news_or_skip_itself(store, channel, monkeypatch, cursor):
    source = await source_row(store, channel, cursor)
    now = store.utcnow()
    items = [RawItem('today', 'https://t.me/news/1', NEWS, date=now.isoformat()),
             RawItem('future', 'https://t.me/news/2', NEWS, date=(now+timedelta(minutes=5)).isoformat())]
    monkeypatch.setattr(scheduler, 'fetch_source', AsyncMock(return_value=(items, 'News')))
    assert await scheduler.poll_source(None, channel, source) == 1
    source = await store.fetch_one('SELECT * FROM sources WHERE id=?', (source['id'],))
    assert source['last_uid'] == 'today'
    monkeypatch.setattr(scheduler, 'now_utc', lambda: now+timedelta(minutes=6))
    assert await scheduler.poll_source(None, channel, source) == 1
    assert len(await store.fetch_all('SELECT * FROM posts')) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disable', 'replace', 'pause'])
async def test_fetch_cannot_commit_after_source_or_channel_changed(store, channel, monkeypatch, change):
    source = await source_row(store, channel)
    async def fetch(*args):
        if change == 'disable':
            await store.execute('UPDATE sources SET enabled=0 WHERE id=?', (source['id'],))
        elif change == 'replace':
            await store.execute("UPDATE sources SET ref='another' WHERE id=?", (source['id'],))
        else:
            await store.execute('UPDATE channels SET paused=1 WHERE id=?', (channel['id'],))
        return [RawItem('new', 'https://t.me/news/1', NEWS)], 'News'
    monkeypatch.setattr(scheduler, 'fetch_source', fetch)
    assert await scheduler.poll_source(None, channel, source) == 0
    assert not await store.fetch_all('SELECT * FROM posts')
    assert (await store.fetch_one('SELECT * FROM sources WHERE id=?', (source['id'],)))['last_uid'] == 'old'


@pytest.mark.asyncio
async def test_lost_cursor_recovers_more_than_five_without_duplicates(store, channel, monkeypatch):
    source = await source_row(store, channel, 'no-longer-in-feed')
    items = [RawItem(str(n), f'https://t.me/news/{n}', NEWS, date=store.utcnow().isoformat()) for n in range(15)]
    monkeypatch.setattr(scheduler, 'fetch_source', AsyncMock(return_value=(items, 'News')))
    assert await scheduler.poll_source(None, channel, source) == 15
    # Rewinding a vanished cursor must not duplicate already inserted items.
    await store.execute("UPDATE sources SET last_uid='no-longer-in-feed' WHERE id=?", (source['id'],))
    assert await scheduler.poll_source(None, channel, source) == 0
    assert len(await store.fetch_all('SELECT * FROM posts')) == 15


@pytest.mark.asyncio
async def test_manual_mode_keeps_short_news_in_review_not_unsendable_digest(store, channel, monkeypatch):
    await store.execute('UPDATE channels SET autopost=0,digest_enabled=1 WHERE id=?', (channel['id'],))
    post = await make_post(store, channel, status='new')
    monkeypatch.setattr(pipeline, 'process', AsyncMock(return_value=pipeline.Result(True, text=NEWS)))
    from app.bot import cards
    monkeypatch.setattr(cards, 'send_moderation_card', AsyncMock())
    await scheduler.process_post(FakeBot(), post, channel)
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (post['id'],)))['status'] == 'pending'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['disable', 'instructions', 'text'])
async def test_digest_rechecks_configuration_and_member_content_after_ai(store, channel, monkeypatch, change):
    await store.execute('UPDATE channels SET autopost=1,digest_enabled=1 WHERE id=?', (channel['id'],))
    post = await make_post(store, channel, status='digest', text=NEWS)
    await store.execute('UPDATE posts SET raw_text=? WHERE id=?', (NEWS, post['id']))
    channel = await store.fetch_one('SELECT * FROM channels WHERE id=?', (channel['id'],))
    async def generate(*args):
        if change == 'disable':
            await store.execute('UPDATE channels SET digest_enabled=0 WHERE id=?', (channel['id'],))
        elif change == 'instructions':
            await store.execute("UPDATE channels SET instructions='Только спорт' WHERE id=?", (channel['id'],))
        else:
            await store.execute("UPDATE posts SET text_out='Другой текст' WHERE id=?", (post['id'],))
        return NEWS
    monkeypatch.setattr(pipeline, 'make_digest', generate)
    monkeypatch.setattr(pipeline, '_factcheck', AsyncMock(return_value={'ok': True}))
    bot = FakeBot()
    await scheduler.run_digest(bot, channel)
    assert not bot.sent
    assert len(await store.fetch_all('SELECT * FROM posts')) == 1
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (post['id'],)))['status'] == 'digest'
