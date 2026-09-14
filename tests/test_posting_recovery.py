import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.types import BufferedInputFile
from app.ai import pipeline, gemini
from app.core import publisher, scheduler
from app.sources.safe_http import UnsafeURL
from tests.test_pipeline_db import FakeBot, make_post


@pytest.mark.asyncio
async def test_signed_cdn_media_is_uploaded_not_passed_as_url(monkeypatch):
    fetch = AsyncMock(return_value=httpx.Response(200, content=b'image bytes', headers={'content-type':'image/jpeg'}))
    monkeypatch.setattr(publisher.safe_http, 'fetch', fetch)
    bot = SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await publisher.publish(bot, {'chat_id':-100}, 'Text', [{'type':'photo','url':'https://example.com/signed.jpg?token=secret'}])
    media = bot.send_photo.call_args.args[1]
    assert isinstance(media, BufferedInputFile)
    assert media.data == b'image bytes'
    assert fetch.call_args.kwargs['max_bytes'] == 10*1024*1024


@pytest.mark.asyncio
async def test_album_is_fully_loaded_before_sending(store, channel, monkeypatch):
    post = await make_post(store, channel)
    await store.execute('UPDATE posts SET media=? WHERE id=?', (json.dumps([{'type':'photo','url':'https://example.com/1.jpg'},{'type':'photo','url':'https://example.com/2.jpg'}]),post['id']))
    fetch = AsyncMock(side_effect=[httpx.Response(200,content=b'photo',headers={'content-type':'image/jpeg'}), UnsafeURL('download refused')])
    monkeypatch.setattr(publisher.safe_http, 'fetch', fetch)
    bot = FakeBot()
    with pytest.raises(ValueError):
        await publisher.publish_post(bot, post, channel)
    assert bot.sent == []
    attempt = await store.fetch_one('SELECT * FROM delivery_attempts WHERE post_id=?',(post['id'],))
    assert attempt['status'] == 'failed' and json.loads(attempt['receipts']) == []
    saved = await store.fetch_one('SELECT status,reason FROM posts WHERE id=?', (post['id'],))
    assert saved['status'] == 'failed' and 'UnsafeURL' in saved['reason']
    assert await publisher.used_today(1) == 0


@pytest.mark.asyncio
async def test_factcheck_uses_fallback_and_still_validates_verdict(monkeypatch):
    generate = AsyncMock(return_value={'ok':True,'hallucinations':[],'distortions':[],'verdict':'Подтверждено'})
    monkeypatch.setattr(gemini, 'generate_json', generate)
    assert (await pipeline._factcheck('original','rewrite',None))['ok']
    assert generate.call_args.kwargs['allow_fallback'] is True
    generate.return_value = {'ok':True, 'hallucinations':['invented'], 'distortions':[], 'verdict':'invalid'}
    assert await pipeline._factcheck('original','rewrite',None) is None


@pytest.mark.asyncio
async def test_provider_outage_defers_without_filtering(store, channel, monkeypatch):
    post = await make_post(store, channel)
    await store.execute("UPDATE posts SET status='new',text_out=NULL,raw_text=? WHERE id=?", ('Достаточно длинная уникальная новость для обработки редакцией.',post['id']))
    post = await store.fetch_one('SELECT * FROM posts WHERE id=?',(post['id'],))
    monkeypatch.setattr(scheduler.pipeline, 'process', AsyncMock(return_value=pipeline.Result(False,retryable=True,reason='фактчек недоступен')))
    await scheduler.process_post(FakeBot(), post, channel)
    fresh = await store.fetch_one('SELECT * FROM posts WHERE id=?',(post['id'],))
    assert fresh['status'] == 'new' and fresh['text_out'] is None
    assert fresh['publish_at'] > store.utcnow()
    assert not await store.fetch_all('SELECT * FROM delivery_attempts')
    assert not await store.fetch_all('SELECT * FROM stats_daily WHERE filtered > 0')


@pytest.mark.asyncio
async def test_publish_now_does_not_send_failed_backlog(store, channel, monkeypatch):
    from app.api.routes import publish_now
    from fastapi import HTTPException
    old = await make_post(store, channel)
    fresh = await make_post(store, channel)
    await store.execute("UPDATE posts SET status='failed'")
    send = AsyncMock(return_value=1)
    monkeypatch.setattr(publisher, 'publish_post', send)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=FakeBot())))
    with pytest.raises(HTTPException) as error:
        await publish_now(channel['id'], request, {'tg_id':1})
    assert error.value.status_code == 409
    send.assert_not_awaited()
