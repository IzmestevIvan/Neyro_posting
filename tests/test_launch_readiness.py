import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from fastapi import HTTPException

from app.ai import pipeline
from app.api import auth, routes
from app.core import scheduler, publisher, runtime
from app.sources.telegram_web import RawItem
from tests.test_auth import sign, valid_fields, TOKEN
from tests.test_pipeline_db import FakeBot, make_post


@pytest.mark.parametrize('payload', [[], None, {'id':True}, {'id':'42'}, {'id':-1}, {'id':2**70}, {'id':42,'username':{}}])
def test_signed_invalid_user_is_unauthorized(monkeypatch, payload):
    monkeypatch.setattr(auth, 'BOT_TOKEN', TOKEN)
    with pytest.raises(HTTPException) as error:
        auth.verify_init_data(sign(valid_fields(user=json.dumps(payload))))
    assert error.value.status_code == 401


def test_future_and_duplicate_auth_fields_are_rejected(monkeypatch):
    monkeypatch.setattr(auth, 'BOT_TOKEN', TOKEN)
    for data in (sign(valid_fields(auth_date=str(int(time.time())+3600))), sign(valid_fields())+'&query_id=AAA'):
        with pytest.raises(HTTPException) as error:
            auth.verify_init_data(data)
        assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_concurrent_registration(store, monkeypatch):
    monkeypatch.setattr(auth, 'BOT_TOKEN', TOKEN)
    users = await asyncio.gather(*(auth.current_user(sign(valid_fields())) for _ in range(30)))
    assert {u['tg_id'] for u in users} == {42}
    assert len(await store.fetch_all('SELECT * FROM users')) == 1


@pytest.mark.asyncio
async def test_alert_survives_database_outage_and_is_quiet(monkeypatch):
    monkeypatch.setattr(scheduler, 'ADMIN_IDS', {99})
    monkeypatch.setattr(scheduler.db, 'get_kv', AsyncMock(side_effect=RuntimeError('database down')))
    monkeypatch.setattr(scheduler.db, 'set_kv', AsyncMock(side_effect=RuntimeError('database down')))
    bot = FakeBot()
    await scheduler.alert_admins(bot, 'database', '<outage>')
    await scheduler.alert_admins(bot, 'database', '<outage>')
    assert len(bot.sent) == 1 and bot.sent[0]['chat'] == 99
    assert '&lt;outage&gt;' in bot.sent[0]['text']


@pytest.mark.asyncio
async def test_source_error_does_not_crash_poller(store, channel, monkeypatch):
    await store.execute("INSERT INTO sources(channel_id,kind,ref,created_at) VALUES(?,'tg','test',now())", (channel['id'],))
    monkeypatch.setattr(scheduler.telegram_web, 'fetch', AsyncMock(side_effect=TimeoutError()))
    await scheduler.poll_channel(None, channel, FakeBot())
    source = await store.fetch_one('SELECT checked_at FROM sources')
    assert source['checked_at'] is not None


@pytest.mark.asyncio
async def test_fresh_button_regenerates_cached_ready_text(store, channel, monkeypatch):
    await store.execute("INSERT INTO sources(channel_id,kind,ref,created_at) VALUES(?,'tg','news',now())", (channel['id'],))
    old = await make_post(store, channel, uid='news/1', text='Старый готовый текст')
    item = RawItem('news/1','https://t.me/news/1','В городе открылась новая библиотека для детей и взрослых.',date=store.utcnow().isoformat())
    monkeypatch.setattr(scheduler.telegram_web, 'fetch', AsyncMock(return_value=([item], 'News')))
    generate = AsyncMock(return_value=pipeline.Result(True, text='Новый текст, созданный сейчас'))
    monkeypatch.setattr(pipeline, 'process', generate)
    bot = FakeBot()
    result = await scheduler.publish_fresh_once(bot, channel)
    assert result['post_id'] == old['id']
    generate.assert_awaited_once()
    assert bot.sent[0]['text'] == 'Новый текст, созданный сейчас'


@pytest.mark.asyncio
async def test_rejection_during_generation_cannot_be_overwritten(store, channel, monkeypatch):
    channel = dict(channel, window_start=0,window_end=24)
    await store.execute('UPDATE channels SET window_start=0,window_end=24 WHERE id=?', (channel['id'],))
    post = await make_post(store, channel, status='new')
    async def generate(*args, **kwargs):
        assert await publisher.reject_post(post['id'])
        return pipeline.Result(True,text='Готовый текст')
    monkeypatch.setattr(pipeline, 'process', generate)
    await scheduler.process_post(FakeBot(), post, channel)
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?',(post['id'],)))['status'] == 'rejected'


@pytest.mark.asyncio
async def test_cleanup_batches_expired_and_preserves_unresolved_deliveries(store, channel):
    await store.execute("INSERT INTO posts(channel_id,status,created_at) SELECT ?, 'expired',now()-interval '30 days' FROM generate_series(1,1201)", (channel['id'],))
    protected = await make_post(store,channel,status='uncertain')
    await store.execute("UPDATE posts SET created_at=now()-interval '180 days' WHERE id=?", (protected['id'],))
    await scheduler.prune_posts()
    left = await store.fetch_all('SELECT id,status FROM posts')
    assert left == [{'id':protected['id'],'status':'uncertain'}]
    assert await store.get_kv('pruned_on') == store.utcnow().date().isoformat()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mark_day_complete(store, monkeypatch):
    monkeypatch.setattr(scheduler, 'expire_news', AsyncMock(side_effect=RuntimeError('failed')))
    with pytest.raises(RuntimeError):
        await scheduler.prune_posts()
    assert await store.get_kv('pruned_on') is None


@pytest.mark.asyncio
async def test_120_channels_are_served_fairly_without_db_pool_starvation(store, monkeypatch):
    await store.execute("INSERT INTO users(tg_id,daily_limit,max_channels,access_until,created_at) "
                        "SELECT n,100,4,now()+interval '30 days',now() FROM generate_series(1,30) n")
    await store.execute("INSERT INTO channels(owner_id,chat_id,autopost,window_start,window_end,created_at) "
                        "SELECT (n-1)/4+1,-100000-n,1,0,24,now() FROM generate_series(1,120) n")
    await store.execute("INSERT INTO posts(channel_id,raw_text,status,created_at) "
                        "SELECT id,'В городе открылась новая библиотека для детей и взрослых.','new',now() FROM channels")
    # A busy first channel must not take all processing slots.
    await store.execute("INSERT INTO posts(channel_id,raw_text,status,created_at) "
                        "SELECT 1,'Другая новость','new',now()-interval '10 minutes' FROM generate_series(1,100)")
    active = peak = 0
    async def generate(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        # This nested DB operation would deadlock if workers held every connection.
        await store.fetch_one('SELECT 1')
        await asyncio.sleep(0.01)
        active -= 1
        return pipeline.Result(True,text='Библиотека открылась',ai_requests=1)
    monkeypatch.setattr(pipeline, 'process', generate)
    started = time.monotonic()
    cursor = 0
    async with asyncio.timeout(30):
        for _ in range(10):
            cursor = await scheduler.process_round(FakeBot(), cursor)
    ready = await store.fetch_all("SELECT DISTINCT channel_id FROM posts WHERE status='approved'")
    assert len(ready) == 120
    assert peak == 3
    bot = FakeBot()
    await scheduler.publish_due(bot)
    assert len(bot.sent) == 120
    assert len({p['chat'] for p in bot.sent}) == 120
    print(f'120 channels / 30 users: processing + publication {time.monotonic()-started:.2f}s; mock AI peak={peak}')


def test_public_channel_contains_no_key_or_internal_paths():
    public = routes.public_channel({'id':1,'gemini_key':'secret','logo_path':'/internal','voice_sample':'private'})
    assert public == {'id':1,'gemini_key_configured':True,'logo_configured':True}


@pytest.mark.asyncio
async def test_pace_is_based_on_delivery_not_queued_slots(store, channel):
    await store.execute("UPDATE channels SET pace='3',autopost=1,window_start=0,window_end=24 WHERE id=?", (channel['id'],))
    channel = await store.fetch_one('SELECT * FROM channels WHERE id=?',(channel['id'],))
    await store.set_kv(f"next_slot:{channel['id']}", '2099-01-01T00:00:00+00:00')
    assert (await scheduler._plan_publish_at(channel)-store.utcnow()).total_seconds()<2
    bot = FakeBot()
    await publisher.publish_post(bot,await make_post(store,channel),channel,background=True)
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot,await make_post(store,channel,text='Следующий'),channel,background=True)
    assert len(bot.sent)==1


@pytest.mark.asyncio
async def test_prune_expired_forward_state(store):
    await store.set_kv('forward:1',{'expires':0})
    await store.set_kv('forward:2',{'expires':time.time()+1000})
    await scheduler.prune_posts()
    assert await store.get_kv('forward:1') is None
    assert await store.get_kv('forward:2') is not None


@pytest.mark.asyncio
async def test_concurrent_channel_add_respects_last_slot(store, owner, monkeypatch):
    await store.execute('UPDATE users SET max_channels=1 WHERE tg_id=?', (owner,))
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=?', (owner,))
    counter = 0
    async def resolve(*args):
        nonlocal counter
        counter += 1
        return -10000-counter, 'Test', f'channel{counter}'
    monkeypatch.setattr(publisher,'resolve_channel',resolve)
    monkeypatch.setattr(publisher,'verify_channel_permissions',AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=FakeBot())))
    results = await asyncio.gather(*(routes.add_channel(request,{'ref':'test'},user) for _ in range(10)), return_exceptions=True)
    assert sum(isinstance(r,dict) for r in results)==1
    assert len(await store.fetch_all('SELECT * FROM channels'))==1


@pytest.mark.asyncio
async def test_health_and_request_budget(store, monkeypatch):
    from app.api.server import create_app
    application = create_app(FakeBot())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url='http://test') as client:
        assert (await client.get('/healthz')).status_code == 200
        assert (await client.post('/api/channels',content=b'x'*65537)).status_code == 413
        monkeypatch.setattr(store,'fetch_one',AsyncMock(side_effect=RuntimeError('DB down')))
        assert (await client.get('/healthz')).status_code == 503


@pytest.mark.asyncio
async def test_shared_donor_is_fetched_once_for_concurrent_channels(monkeypatch):
    fetch = AsyncMock(return_value=([], 'News'))
    monkeypatch.setattr(scheduler.telegram_web,'fetch',fetch)
    await asyncio.gather(*(scheduler.fetch_source(None, {'kind':'tg','ref':'news'}) for _ in range(120)))
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_30_simultaneous_api_users(store, monkeypatch):
    from app.api.server import create_app
    monkeypatch.setattr(auth,'BOT_TOKEN',TOKEN)
    application = create_app(FakeBot())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url='http://test') as client:
        async def open_panel(user_id):
            data=sign(valid_fields(user=json.dumps({'id':user_id})))
            return await client.get('/api/bootstrap',headers={'x-init-data':data})
        replies=await asyncio.gather(*(open_panel(i) for i in range(1,31)))
    assert all(r.status_code==200 for r in replies)
    assert {r.json()['user']['id'] for r in replies}==set(range(1,31))
