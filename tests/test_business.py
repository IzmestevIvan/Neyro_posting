import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.ai import gemini
from app.api.routes import _clean_settings, update_channel, business_draft, post_action
from app.core import business, publisher, scheduler
from tests.test_pipeline_db import FakeBot, make_post

TEXT = 'Перед оснащением переговорной определите число участников, сценарии встреч и требования к звуку. Это поможет составить понятное техническое задание.'

@pytest.fixture(autouse=True)
def offline_research(monkeypatch):
    monkeypatch.setattr(business, 'research_company', AsyncMock(return_value={}))


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit', [False, True])
async def test_unreadable_website_only_blocks_explicit_reference(store,channel,monkeypatch,explicit):
    current = await enable(store,channel)
    current['business_profile'] = business.clean_profile({'name':'DOBRA','services':'Связь','website':'https://example.org'})
    monkeypatch.setattr(business.web,'fetch_article',AsyncMock(side_effect=ValueError('не удалось извлечь текст')))
    ai = AsyncMock(return_value={'scope':'company','basis':'Услуги из досье','text':TEXT,'review':'Проверьте факты'})
    monkeypatch.setattr(gemini,'generate_json',ai)
    if explicit:
        with pytest.raises(ValueError,match='очистите ссылку'):
            await business.draft_text(current,'Наш проект', 'https://example.org/project')
        ai.assert_not_called()
    else:
        text, review, url = await business.draft_text(current,'Наш проект', '   ')
        assert text==TEXT and not url and 'не удалось прочитать' in review
        prompt = json.loads(ai.call_args.args[0])
        assert prompt['owner_request']=='Наш проект' and prompt['unverified_web_reference']==''


@pytest.mark.asyncio
async def test_statistics_failure_does_not_hide_saved_draft(store,channel,monkeypatch):
    await enable(store,channel)
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(return_value={'scope':'company','basis':'Услуги из досье','text':TEXT,'review':''}))
    monkeypatch.setattr(store,'bump_stat',AsyncMock(side_effect=RuntimeError('stats unavailable')))
    result = await business.generate(channel['id'])
    assert result['status']=='pending'
    assert (await store.fetch_one('SELECT count(*) AS n FROM posts'))['n']==1


async def enable(store, channel, auto=False):
    profile = business.clean_profile({'name': 'DOBRA', 'services': 'Мультимедиа и связь'})
    await store.execute('UPDATE channels SET business_mode=1,business_auto=?,business_profile=? WHERE id=?',
                        (int(auto),profile,channel['id']))
    return await store.fetch_one('SELECT * FROM channels WHERE id=?',(channel['id'],))


@pytest.mark.parametrize('profile', [[], {'secret':'x'}, {'name':123}, {'facts':'x'*3001}, {'website':'file:///etc/passwd'}, {'website':'https://user:pass@example.com'}])
def test_profile_validation(profile):
    with pytest.raises(HTTPException):
        _clean_settings({'business_profile':profile})


@pytest.mark.asyncio
async def test_generates_pending_without_sources(store, channel, monkeypatch):
    await enable(store, channel)
    ai = AsyncMock(return_value={'scope':'company','basis':'Услуги из досье','text':TEXT,'review':'Проверьте терминологию'})
    monkeypatch.setattr(gemini, 'generate_json', ai)
    result = await business.generate(channel['id'])
    post = await store.fetch_one('SELECT * FROM posts WHERE id=?',(result['post_id'],))
    assert post['status']=='pending' and post['is_manual'] and post['business_draft'] and post['business_generated']
    assert post['publish_at'] is None and post['text_out']==TEXT
    assert 'DOBRA' in ai.call_args.args[0]
    with pytest.raises(ValueError, match='минуту'):
        await business.generate(channel['id'])
    assert ai.await_count == 1


@pytest.mark.asyncio
async def test_provider_failure_no_post_and_cooldown(store, channel, monkeypatch):
    await enable(store, channel, True)
    ai = AsyncMock(side_effect=gemini.AIError('unavailable'))
    monkeypatch.setattr(gemini,'generate_json',ai)
    await business.propose_due()
    await business.propose_due()
    assert ai.await_count == 1
    assert not await store.fetch_all('SELECT * FROM posts')


@pytest.mark.asyncio
async def test_dossier_changed_while_generating_discards_result(store, channel, monkeypatch):
    await enable(store, channel)
    async def ai(*args, **kwargs):
        await store.execute("UPDATE channels SET business_profile='{}' WHERE id=?",(channel['id'],))
        return {'scope':'company','basis':'Услуги из досье','text':TEXT,'review':''}
    monkeypatch.setattr(gemini,'generate_json',ai)
    with pytest.raises(ValueError, match='изменились'):
        await business.generate(channel['id'])
    assert not await store.fetch_all('SELECT * FROM posts')


@pytest.mark.asyncio
async def test_five_pending_stops_generation(store, channel, monkeypatch):
    await enable(store, channel)
    for i in range(5):
        p = await make_post(store,channel,status='pending',text=f'{TEXT} {i}')
        await store.execute('UPDATE posts SET business_draft=1,business_generated=1 WHERE id=?',(p['id'],))
    ai = AsyncMock()
    monkeypatch.setattr(gemini,'generate_json',ai)
    with pytest.raises(ValueError, match='Лимит'):
        await business.generate(channel['id'])
    ai.assert_not_called()


@pytest.mark.asyncio
async def test_business_requires_explicit_approval_even_if_autopost_enabled(store, channel):
    current = await enable(store,channel)
    await store.execute('UPDATE channels SET autopost=1 WHERE id=?',(channel['id'],))
    post = await make_post(store,channel,status='pending',text=TEXT)
    bot = FakeBot()
    for options in ({}, {'background':True}, {'background':True,'approved_by_user':True}):
        with pytest.raises(publisher.AlreadyPublished):
            await publisher.publish_post(bot,post,channel,**options)
    assert not bot.sent
    with pytest.raises(publisher.AlreadyPublished, match='Текст изменился'):
        await publisher.publish_post(bot,post,current,approved_by_user=True,approved_text='Старая версия')
    await publisher.publish_post(bot,post,current,approved_by_user=True,approved_text=TEXT)
    assert len(bot.sent)==1


@pytest.mark.asyncio
async def test_business_flag_survives_mode_switch(store,channel):
    post = await make_post(store,channel,status='pending',text=TEXT)
    await store.execute('UPDATE posts SET business_draft=1,is_manual=1 WHERE id=?',(post['id'],))
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(FakeBot(),post,channel)


@pytest.mark.asyncio
async def test_old_publish_now_checks_actual_mode(store,channel):
    await enable(store,channel)
    with pytest.raises(ValueError,match='Бизнес-режим'):
        await scheduler.publish_fresh_once(FakeBot(),channel)


@pytest.mark.asyncio
async def test_enabling_mode_moves_queue_to_review_and_disables_auto(store,channel):
    await make_post(store,channel,status='approved',text=TEXT)
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    updated = await update_channel(channel['id'],{'business_mode':True,'autopost':True,'digest_enabled':True},user)
    assert updated['autopost']==0 and updated['digest_enabled']==0
    post = await store.fetch_one('SELECT * FROM posts')
    assert post['status']=='pending' and post['business_draft']==1
    assert post['business_generated']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic', [False, True])
async def test_imported_news_do_not_consume_company_generation_limit(store,channel,monkeypatch,automatic):
    for i in range(20):
        await make_post(store,channel,status='approved',text=f'Старая новость {i}')
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    await update_channel(channel['id'],{'business_mode':True},user)
    await enable(store,channel,automatic)
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(return_value={'scope':'company','basis':'Услуги из досье','text':TEXT,'review':''}))
    if automatic:
        await business.propose_due()
    else:
        await business.generate(channel['id'])
    rows = await store.fetch_all('SELECT status,business_generated FROM posts')
    assert len(rows)==21 and sum(r['business_generated'] for r in rows)==1
    assert all(r['status']=='pending' for r in rows)


@pytest.mark.asyncio
async def test_rejected_generated_drafts_still_count_toward_daily_limit(store,channel,monkeypatch):
    await enable(store,channel)
    for i in range(5):
        post = await make_post(store,channel,status='rejected',text=f'{TEXT} {i}')
        await store.execute('UPDATE posts SET business_generated=1,business_draft=1 WHERE id=?',(post['id'],))
    ai = AsyncMock()
    monkeypatch.setattr(gemini,'generate_json',ai)
    with pytest.raises(ValueError,match='24 часа'):
        await business.generate(channel['id'])
    ai.assert_not_called()


@pytest.mark.asyncio
async def test_business_endpoint_checks_owner(store,channel):
    with pytest.raises(HTTPException) as error:
        await business_draft(channel['id'],{}, {'tg_id':999, 'is_admin':0})
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_private_url_is_not_read(store,channel):
    current = await enable(store,channel)
    with pytest.raises(ValueError):
        await business.draft_text(current,article_url='http://127.0.0.1/secret')


@pytest.mark.asyncio
async def test_edit_then_approve_requires_visible_version(store,channel):
    await enable(store,channel)
    post = await make_post(store,channel,status='pending',text=TEXT)
    await store.execute('UPDATE posts SET business_draft=1,is_manual=1 WHERE id=?',(post['id'],))
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=1')
    bot = FakeBot()
    updated = TEXT + ' Уточните также состав участников.'
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)),json=AsyncMock(return_value={'text':updated}))
    await post_action(post['id'],'edit',request,user)
    request.json.return_value = {'text':TEXT}
    with pytest.raises(HTTPException) as error:
        await post_action(post['id'],'approve',request,user)
    assert error.value.status_code==409 and not bot.sent
    request.json.return_value = {'text':updated}
    await post_action(post['id'],'approve',request,user)
    assert len(bot.sent)==1


@pytest.mark.asyncio
async def test_malformed_generation_never_becomes_draft(store,channel,monkeypatch):
    await enable(store,channel)
    monkeypatch.setattr(gemini,'generate_json',AsyncMock(return_value={'text':['bad'],'review':''}))
    with pytest.raises(gemini.AIError):
        await business.generate(channel['id'])
    assert not await store.fetch_all('SELECT * FROM posts')


@pytest.mark.asyncio
async def test_auto_uses_daily_interval_and_pause(store,channel,monkeypatch):
    await enable(store,channel,True)
    ai = AsyncMock(return_value={'scope':'company','basis':'Услуги из досье','text':TEXT,'review':''})
    monkeypatch.setattr(gemini,'generate_json',ai)
    await store.execute('UPDATE channels SET paused=1 WHERE id=?',(channel['id'],))
    await business.propose_due()
    ai.assert_not_called()
    await store.execute('UPDATE channels SET paused=0 WHERE id=?',(channel['id'],))
    await business.propose_due()
    await business.propose_due()
    assert ai.await_count==1
    assert (await store.fetch_one('SELECT status FROM posts'))['status']=='pending'


@pytest.mark.asyncio
async def test_web_reference_is_data_not_claimed_factcheck(store,channel,monkeypatch):
    current = await enable(store,channel)
    article = AsyncMock(return_value=SimpleNamespace(text='Материал официальной страницы', media=[]))
    monkeypatch.setattr(business.web,'fetch_article',article)
    ai = AsyncMock(return_value={'scope':'company','basis':'Услуги из досье','text':TEXT,'review':'Проверьте факты страницы'})
    monkeypatch.setattr(gemini,'generate_json',ai)
    text, review, url = await business.draft_text(current,article_url='https://example.org/article')
    assert json.loads(ai.call_args.args[0])['unverified_web_reference']=='Материал официальной страницы'
    assert url=='https://example.org/article' and text==TEXT and 'согласование' in review
