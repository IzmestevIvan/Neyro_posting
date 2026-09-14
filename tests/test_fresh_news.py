from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from app.core import scheduler
from app.bot import cards
from app.ai import pipeline
from app.sources.telegram_web import RawItem
from tests.test_pipeline_db import make_post, FakeBot

TEXT = 'В городе открылась новая библиотека. Вход для посетителей бесплатный.'


@pytest.mark.asyncio
async def test_disabled_filter_rechecks_only_recent_matching_rejections(store, channel):
    ids = []
    for uid, reason, status in [('hit', 'ниже медианы источника (2<100)', 'filtered'),
                                ('media', 'нет медиа', 'filtered'),
                                ('ad', 'реклама', 'filtered'),
                                ('old', 'ниже медианы источника (2<100)', 'filtered'),
                                ('sent', 'ниже медианы источника (2<100)', 'published')]:
        post = await make_post(store, channel, status=status, uid=uid)
        ids.append(post['id'])
        await store.execute('UPDATE posts SET reason=? WHERE id=?', (reason, post['id']))
    await store.execute("UPDATE posts SET created_at=now()-interval '2 days' WHERE id=?", (ids[3],))
    await store.execute('UPDATE channels SET hits_only=0,media_only=1 WHERE id=?', (channel['id'],))
    assert await scheduler.reconsider_filtered(channel['id']) == 1
    rows = await store.fetch_all('SELECT status FROM posts ORDER BY id')
    assert [r['status'] for r in rows] == ['new','filtered','filtered','filtered','published']
    assert await scheduler.reconsider_filtered(channel['id']) == 0


@pytest.mark.asyncio
async def test_night_poll_advances_cursor_without_stockpiling(store, channel, monkeypatch):
    clock = datetime(2026,9,10,0,0,tzinfo=timezone.utc)
    monkeypatch.setattr(scheduler, 'now_utc', lambda: clock)
    source_id = await store.insert("INSERT INTO sources(channel_id,kind,ref,created_at) VALUES(?,'tg','news',now())", (channel['id'],))
    source = await store.fetch_one('SELECT * FROM sources WHERE id=?',(source_id,))
    item = RawItem('news/1','https://t.me/news/1',TEXT,date=clock.isoformat())
    monkeypatch.setattr(scheduler.telegram_web,'fetch',AsyncMock(return_value=([item],'News')))
    assert await scheduler.poll_source(None,channel,source) == 0
    assert not await store.fetch_all('SELECT * FROM posts')
    assert (await store.fetch_one('SELECT * FROM sources WHERE id=?',(source_id,)))['last_uid'] == 'news/1'


@pytest.mark.asyncio
async def test_expiration_retains_manual_and_uncertain(store, channel):
    for status in ('approved','failed','pending','uncertain','published'):
        await make_post(store,channel,status=status)
    manual = await make_post(store,channel,status='pending',uid='manual')
    await store.execute("UPDATE posts SET created_at=now()-interval '3 hours'")
    await store.execute('UPDATE posts SET is_manual=1 WHERE id=?',(manual['id'],))
    assert await scheduler.expire_news() == 3
    saved = await store.fetch_all('SELECT status FROM posts')
    assert sorted(p['status'] for p in saved) == sorted(['expired']*3+['uncertain','published','pending'])


@pytest.mark.asyncio
async def test_background_cards_silent_at_night_and_in_autopost(store,channel,monkeypatch):
    post = await make_post(store,channel,status='pending')
    bot = FakeBot()
    monkeypatch.setattr(cards.news_policy,'window_open',lambda c:False)
    await cards.send_moderation_card(bot,channel,post['id'])
    assert not bot.sent
    monkeypatch.setattr(cards.news_policy,'window_open',lambda c:True)
    await store.execute('UPDATE channels SET autopost=1 WHERE id=?',(channel['id'],))
    await cards.send_moderation_card(bot,channel,post['id'])
    assert not bot.sent
    assert channel['title'] in cards.card_text(post,channel)


@pytest.mark.asyncio
@pytest.mark.parametrize('manual', [0, 1])
async def test_automatic_review_queue_never_sends_chat_cards(store, channel, manual):
    post = await make_post(store, channel, status='pending')
    await store.execute('UPDATE posts SET is_manual=? WHERE id=?', (manual, post['id']))
    bot = FakeBot()
    await cards.send_moderation_card(bot, channel, post['id'])
    assert not bot.sent


@pytest.mark.asyncio
async def test_legacy_weekly_report_never_pushes_to_chat():
    bot = FakeBot()
    await scheduler.weekly_report(bot)
    assert not bot.sent


@pytest.mark.asyncio
async def test_manual_submission_only_acknowledges_and_keeps_text_in_app(store, channel, monkeypatch):
    from app.bot import handlers
    monkeypatch.setattr(handlers, 'ensure_user', AsyncMock())
    monkeypatch.setattr(handlers, 'active_channel', AsyncMock(return_value=channel))
    monkeypatch.setattr(handlers.pipeline, 'process', AsyncMock(return_value=pipeline.Result(True, text=TEXT)))
    notice = SimpleNamespace(message_id=101, edit_text=AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=channel['owner_id']),
                              reply_to_message=None, text=TEXT, caption=None,
                              photo=None, video=None, message_id=100,
                              answer=AsyncMock(return_value=notice))
    await handlers.on_manual(message, FakeBot())
    notice.edit_text.assert_awaited_once()
    assert TEXT not in notice.edit_text.call_args.args[0]
    assert 'приложении' in notice.edit_text.call_args.args[0]
    post = await store.fetch_one('SELECT * FROM posts WHERE channel_id=?', (channel['id'],))
    assert post['text_out'] == TEXT
    assert post['status'] == 'pending'


@pytest.mark.asyncio
async def test_fresh_button_sends_one_outside_schedule(store,channel,monkeypatch):
    await store.execute('UPDATE channels SET paused=1,autopost=1,window_start=0,window_end=1 WHERE id=?',(channel['id'],))
    channel = await store.fetch_one('SELECT * FROM channels WHERE id=?',(channel['id'],))
    await store.insert("INSERT INTO sources(channel_id,kind,ref,created_at) VALUES(?,'tg','news',now())", (channel['id'],))
    now = store.utcnow()
    items = [RawItem(f'news/{i}',f'https://t.me/news/{i}',TEXT+str(i),date=(now-timedelta(minutes=i)).isoformat()) for i in (1,2)]
    monkeypatch.setattr(scheduler.telegram_web,'fetch',AsyncMock(return_value=(items,'News')))
    monkeypatch.setattr(pipeline,'process',AsyncMock(return_value=pipeline.Result(True,text=TEXT)))
    bot = FakeBot()
    result = await scheduler.publish_fresh_once(bot,channel)
    assert result['ok']
    assert len(bot.sent)==1 and bot.sent[0]['chat']==channel['chat_id']
    rows = await store.fetch_all('SELECT * FROM posts')
    assert len(rows)==1 and rows[0]['status']=='published'
    assert rows[0]['uid']=='news/1'
    assert (await store.fetch_one('SELECT paused FROM channels WHERE id=?',(channel['id'],)))['paused']==1


@pytest.mark.asyncio
async def test_old_source_never_resurrected_by_button(store,channel,monkeypatch):
    await store.insert("INSERT INTO sources(channel_id,kind,ref,created_at) VALUES(?,'tg','news',now())", (channel['id'],))
    item = RawItem('news/1','https://t.me/news/1',TEXT,date=(store.utcnow()-timedelta(days=1)).isoformat())
    monkeypatch.setattr(scheduler.telegram_web,'fetch',AsyncMock(return_value=([item],'News')))
    bot=FakeBot()
    with pytest.raises(ValueError):
        await scheduler.publish_fresh_once(bot,channel)
    assert not bot.sent
    assert not await store.fetch_all('SELECT * FROM posts')


@pytest.mark.asyncio
async def test_quick_add_reports_duplicates_without_refetch(store,channel,monkeypatch):
    from app.api.routes import add_source
    fetch = AsyncMock(return_value=([], 'Example'))
    monkeypatch.setattr(scheduler.telegram_web, 'fetch', fetch)
    first = await add_source(channel['id'], {'ref':'@Example'}, {'tg_id':1})
    second = await add_source(channel['id'], {'ref':'https://t.me/Example/1'}, {'tg_id':1})
    assert not first['already_exists'] and second['already_exists']
    assert first['id'] == second['id']
    fetch.assert_awaited_once()
