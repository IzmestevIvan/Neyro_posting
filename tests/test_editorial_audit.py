from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai import gemini, pipeline
from app.core import scheduler, publisher
from app.core.filters import fingerprint, find_duplicate
from tests.test_pipeline_db import FakeBot, make_post

TEXT = 'В городе открыли новый общественный парк с дорожками и площадкой для прогулок.'


def test_same_quote_without_source_credit_and_yo_noise():
    first = 'Россия должна быть или великой державой, или ее вообще не будет, заявил Медведев.\n\nВидео: Официальный канал Дмитрия Медведева/ТАСС\n\nПодпишись на ТАСС'
    second = 'Россия может быть или великой державой, или её вовсе не будет — таковы исторические законы, — Медведев.\n\nТопор Live. Подписаться'
    assert find_duplicate(fingerprint(second), [(1, fingerprint(first))]) == 1


@pytest.mark.asyncio
async def test_semantic_duplicate_skips_rewrite(monkeypatch):
    triage = AsyncMock(return_value={'is_ad':False,'is_offtopic':False,'is_newsworthy':True,'duplicate_of':42})
    rewrite = AsyncMock()
    monkeypatch.setattr(gemini, 'generate_json', triage)
    monkeypatch.setattr(gemini, 'generate', rewrite)
    result = await pipeline.process(TEXT,quality='super',instructions='',lang='ru',api_key=None,
                                    recent_posts=[{'id':42,'text_out':TEXT}])
    assert not result.ok and result.duplicate_of == 42
    assert TEXT in triage.call_args.args[0]
    rewrite.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_duplicate_id_does_not_discard_post(monkeypatch):
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(return_value={
        'is_ad':False,'is_offtopic':False,'is_newsworthy':True,'duplicate_of':999}))
    result = await pipeline.process(TEXT,quality='super',instructions='',lang='ru',api_key=None,
                                    recent_posts=[{'id':42,'text_out':TEXT}])
    assert not result.ok and result.retryable and result.duplicate_of is None


@pytest.mark.asyncio
@pytest.mark.parametrize('quality',['fast','balanced','super'])
async def test_empty_rewrite_is_never_ready(monkeypatch,quality):
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(return_value={
        'is_ad':False,'is_offtopic':False,'is_newsworthy':True}))
    monkeypatch.setattr(gemini,'generate',AsyncMock(return_value=' '))
    result = await pipeline.process(TEXT,quality=quality,instructions='',lang='ru',api_key=None)
    assert not result.ok and result.retryable


@pytest.mark.asyncio
async def test_latest_channel_settings_used_before_processing(store,channel,monkeypatch):
    post=await make_post(store,channel,status='new')
    await store.execute('UPDATE posts SET raw_text=? WHERE id=?',(TEXT,post['id']))
    await store.execute('UPDATE channels SET stopwords=? WHERE id=?',('парк',channel['id']))
    process=AsyncMock()
    monkeypatch.setattr(pipeline,'process',process)
    await scheduler.process_post(FakeBot(),post,channel)
    saved=await store.fetch_one('SELECT * FROM posts WHERE id=?',(post['id'],))
    assert saved['status']=='filtered' and 'стоп-слово' in saved['reason']
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_editorial_settings_recheck_result(store,channel,monkeypatch):
    post=await make_post(store,channel,status='new')
    await store.execute('UPDATE posts SET raw_text=? WHERE id=?',(TEXT,post['id']))
    async def process(*args,**kwargs):
        await store.execute('UPDATE channels SET instructions=? WHERE id=?',('Только спорт',channel['id']))
        return pipeline.Result(True,text=TEXT)
    monkeypatch.setattr(pipeline,'process',process)
    await scheduler.process_post(FakeBot(),post,channel)
    saved=await store.fetch_one('SELECT * FROM posts WHERE id=?',(post['id'],))
    assert saved['status']=='new' and saved['publish_at']>store.utcnow()
    assert 'Настройки контента' in saved['reason']


@pytest.mark.asyncio
async def test_cdn_retry_happens_before_single_send(monkeypatch):
    request=httpx.Request('GET','https://example.com/photo.jpg')
    error=httpx.HTTPStatusError('unavailable',request=request,response=httpx.Response(503,request=request))
    fetch=AsyncMock(side_effect=[error,httpx.Response(200,content=b'photo',headers={'content-type':'image/jpeg'})])
    monkeypatch.setattr(publisher.safe_http,'fetch',fetch)
    monkeypatch.setattr(publisher.asyncio,'sleep',AsyncMock())
    bot=FakeBot()
    await publisher.publish(bot,{'chat_id':-100},TEXT,[{'type':'photo','url':str(request.url)}])
    assert len(bot.sent)==1 and fetch.await_count==2


@pytest.mark.asyncio
async def test_backup_success_keeps_primary_on_pause(monkeypatch):
    monkeypatch.setattr(gemini,'GEMINI_API_KEY','test')
    monkeypatch.setattr(gemini.key_pool,'candidates',AsyncMock(return_value=[]))
    monkeypatch.setattr(gemini,'cooldown_left',AsyncMock(return_value=0))
    monkeypatch.setattr(gemini,'_start_cooldown',AsyncMock())
    monkeypatch.setattr(gemini.asyncio,'sleep',AsyncMock())
    call=AsyncMock(side_effect=[gemini.AIError('HTTP 429'),'first backup','second backup'])
    monkeypatch.setattr(gemini,'_call',call)
    assert await gemini.generate(TEXT)=='first backup'
    assert await gemini.generate(TEXT)=='second backup'
    assert [c.args[1] for c in call.call_args_list]==[
        gemini.GEMINI_MODEL_MAIN,gemini.GEMINI_MODEL_FALLBACK,gemini.GEMINI_MODEL_FALLBACK]
    call.return_value='customer'; call.side_effect=None
    assert await gemini.generate(TEXT,api_key='other')=='customer'
    assert call.call_args.args[1]==gemini.GEMINI_MODEL_MAIN


@pytest.mark.asyncio
async def test_rejection_reports_final_factcheck(monkeypatch):
    triage={'is_ad':False,'is_offtopic':False,'is_newsworthy':True}
    first={'ok':False,'hallucinations':['первая выдумка'],'distortions':[],'verdict':'первый'}
    second={'ok':False,'hallucinations':[],'distortions':['новое искажение'],'verdict':'повторный'}
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(side_effect=[triage,first,second]))
    monkeypatch.setattr(gemini,'generate',AsyncMock(return_value=TEXT))
    result=await pipeline.process(TEXT,quality='super',instructions='',lang='ru',api_key=None)
    assert not result.ok and result.fact_check==second
    assert 'новое искажение' in result.reason and 'первая выдумка' not in result.reason


@pytest.mark.asyncio
async def test_expired_model_pause_allows_primary_again(monkeypatch):
    import hashlib
    monkeypatch.setattr(gemini,'GEMINI_API_KEY','test')
    monkeypatch.setattr(gemini.key_pool,'candidates',AsyncMock(return_value=[]))
    monkeypatch.setattr(gemini,'cooldown_left',AsyncMock(return_value=0))
    key=(hashlib.sha256(b'test').hexdigest(),gemini.GEMINI_MODEL_MAIN,False)
    gemini._model_pauses[key]=gemini.time.monotonic()-1
    call=AsyncMock(return_value=TEXT)
    monkeypatch.setattr(gemini,'_call',call)
    assert await gemini.generate(TEXT)==TEXT
    assert call.call_args.args[1]==gemini.GEMINI_MODEL_MAIN
    assert key not in gemini._model_pauses


@pytest.mark.asyncio
async def test_permanent_media_error_is_not_retried(monkeypatch):
    request=httpx.Request('GET','https://example.com/photo.jpg')
    error=httpx.HTTPStatusError('missing',request=request,response=httpx.Response(404,request=request))
    fetch=AsyncMock(side_effect=error)
    monkeypatch.setattr(publisher.safe_http,'fetch',fetch)
    bot=FakeBot()
    with pytest.raises(httpx.HTTPStatusError):
        await publisher.publish(bot,{'chat_id':-100},TEXT,[{'type':'photo','url':str(request.url)}])
    assert not bot.sent and fetch.await_count==1


@pytest.mark.asyncio
async def test_expiration_preserves_processing_failure(store,channel):
    post=await make_post(store,channel,status='new')
    await store.execute("UPDATE posts SET created_at=now()-interval '2 days',reason='фактчек недоступен' WHERE id=?",(post['id'],))
    await scheduler.expire_news()
    saved=await store.fetch_one('SELECT * FROM posts WHERE id=?',(post['id'],))
    assert saved['status']=='expired'
    assert 'истёк срок актуальности' in saved['reason']
    assert 'фактчек недоступен' in saved['reason']


@pytest.mark.asyncio
async def test_busy_routes_retry_soon_without_delivery(store,channel,monkeypatch):
    post=await make_post(store,channel,status='new')
    await store.execute('UPDATE posts SET raw_text=? WHERE id=?',(TEXT,post['id']))
    monkeypatch.setattr(pipeline,'process',AsyncMock(side_effect=gemini.BusyError('busy')))
    await scheduler.process_post(FakeBot(),post,channel)
    saved=await store.fetch_one('SELECT * FROM posts WHERE id=?',(post['id'],))
    assert saved['status']=='new'
    assert 0 < (saved['publish_at']-store.utcnow()).total_seconds() <= 30
    assert not await store.fetch_all('SELECT * FROM delivery_attempts')


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['triage','factcheck'])
async def test_busy_check_is_not_converted_to_long_outage(monkeypatch,stage):
    triage={'is_ad':False,'is_offtopic':False,'is_newsworthy':True}
    results=[gemini.BusyError('busy')] if stage=='triage' else [triage,gemini.BusyError('busy')]
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(side_effect=results))
    monkeypatch.setattr(gemini,'generate',AsyncMock(return_value=TEXT))
    with pytest.raises(gemini.BusyError):
        await pipeline.process(TEXT,quality='super',instructions='',lang='ru',api_key=None)


@pytest.mark.asyncio
async def test_business_failure_is_visible_without_provider_secrets(store,channel,monkeypatch):
    from app.core import business, features
    monkeypatch.setattr(features, 'BUSINESS_MODE_ENABLED', True)
    from app.api.routes import channel_stats
    await store.execute('UPDATE channels SET business_mode=1,business_auto=1 WHERE id=?',(channel['id'],))
    monkeypatch.setattr(business,'generate',AsyncMock(side_effect=gemini.AIError('secret provider detail')))
    await business.propose_due()
    state=await store.get_kv(f'business_error:{channel["id"]}')
    assert 'Черновик не создан' in state['reason'] and 'secret' not in state['reason']
    user=await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    stats=await channel_stats(channel['id'],user)
    assert any('Последняя подготовка' in text for text in stats['blockers'])
