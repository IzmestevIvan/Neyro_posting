import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core import scheduler, business
from app.core import publisher
from datetime import timedelta
from app.api.routes import channel_stats
from tests.test_pipeline_db import FakeBot, make_post


@pytest.mark.asyncio
async def test_business_does_not_collect_old_news(store,channel,monkeypatch):
    channel = dict(channel, business_mode=1)
    fetch = AsyncMock()
    monkeypatch.setattr(scheduler,'poll_source',fetch)
    await scheduler.poll_channel(None,channel)
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_business_manual_uses_dossier_not_news_filter(store,channel,monkeypatch):
    await store.execute('UPDATE channels SET business_mode=1 WHERE id=?',(channel['id'],))
    post = await make_post(store,channel,status='new')
    await store.execute('UPDATE posts SET is_manual=1 WHERE id=?',(post['id'],))
    draft = AsyncMock(return_value=('Готовый материал компании','Согласуйте факты',''))
    monkeypatch.setattr(business,'draft_text',draft)
    news = AsyncMock()
    monkeypatch.setattr(scheduler.pipeline,'process',news)
    await scheduler.process_post(FakeBot(),post,channel)
    updated = await store.fetch_one('SELECT * FROM posts WHERE id=?',(post['id'],))
    assert updated['status']=='pending' and updated['business_draft']
    news.assert_not_called()
    draft.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_channel_diagnostics_explain_setup(store,channel):
    await store.execute('UPDATE channels SET paused=1 WHERE id=?',(channel['id'],))
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    stats = await channel_stats(channel['id'],user)
    assert any('на паузе' in s for s in stats['blockers'])
    assert any('выключен' in s for s in stats['blockers'])
    assert any('Нет включённых источников' in s for s in stats['blockers'])


@pytest.mark.asyncio
async def test_slow_channel_does_not_hold_other_processing_slots(store,channel,monkeypatch):
    ids = [channel['id']]
    for n in range(4):
        ids.append(await store.insert("INSERT INTO channels(owner_id,chat_id,window_start,window_end,created_at) VALUES(1,?,0,24,now())",(-500-n,)))
    for cid in ids:
        await make_post(store,{'id':cid},status='new',text=str(cid))
    slow = asyncio.Event()
    progressed = asyncio.Event()
    served = set()
    async def process(bot,post,c):
        if c['id']==ids[0]:
            await slow.wait()
        served.add(c['id'])
        await store.execute("UPDATE posts SET status='pending' WHERE id=?",(post['id'],))
        if len(served)==4:
            progressed.set()
    monkeypatch.setattr(scheduler,'process_post',process)
    task = asyncio.create_task(scheduler.process_loop(FakeBot()))
    try:
        await asyncio.wait_for(progressed.wait(),10)
        assert ids[0] not in served and len(served)==4
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_background_rechecks_rescheduled_time(store,channel):
    await store.execute('UPDATE channels SET autopost=1 WHERE id=?',(channel['id'],))
    post = await make_post(store,channel)
    await store.execute('UPDATE posts SET publish_at=? WHERE id=?',(store.utcnow()+timedelta(hours=1),post['id']))
    with pytest.raises(publisher.AlreadyPublished,match='Время публикации'):
        await publisher.publish_post(FakeBot(),post,channel,background=True)


@pytest.mark.asyncio
async def test_slow_delivery_does_not_block_other_channels(store,channel,monkeypatch):
    ids = [channel['id']]
    await store.execute('UPDATE channels SET autopost=1 WHERE id=?',(channel['id'],))
    for n in range(3):
        ids.append(await store.insert("INSERT INTO channels(owner_id,chat_id,autopost,window_start,window_end,created_at) VALUES(1,?,1,0,24,now())",(-900-n,)))
    for cid in ids:
        await make_post(store,{'id':cid})
    hold = asyncio.Event()
    finished = asyncio.Event()
    served = set()
    async def send(bot,post,c,**kwargs):
        if c['id']==ids[0]: await hold.wait()
        await store.execute("UPDATE posts SET status='published',published_at=now() WHERE id=?",(post['id'],))
        served.add(c['id'])
        if len(served)==3: finished.set()
    monkeypatch.setattr(publisher,'publish_post',send)
    task = asyncio.create_task(scheduler.publish_loop(FakeBot()))
    try:
        await asyncio.wait_for(finished.wait(),12)
        assert ids[0] not in served
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
