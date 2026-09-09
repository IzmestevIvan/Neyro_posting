from unittest.mock import AsyncMock
import pytest

from app.ai import pipeline
from app.api.routes import list_ads, set_ad_status
from app.core import adbook, scheduler

NEWS = 'Рекламодатели обсудили новые правила рынка. Участники конференции предложили изменить отчётность.'


@pytest.mark.asyncio
async def test_keyword_does_not_archive_news(monkeypatch):
    monkeypatch.setattr(pipeline.gemini, 'generate_json', AsyncMock(return_value={
        'decision': 'not_ad', 'reason': 'Новость об отраслевой конференции', 'evidence': ''}))
    monkeypatch.setattr(pipeline.gemini, 'generate', AsyncMock(return_value=NEWS))
    result = await pipeline.process(NEWS, quality='fast', instructions='', lang='ru', api_key='test')
    assert result.ok and not result.is_ad and not result.needs_review


@pytest.mark.asyncio
async def test_invented_evidence_requires_review(monkeypatch):
    monkeypatch.setattr(pipeline.gemini, 'generate_json', AsyncMock(return_value={
        'decision':'ad', 'reason':'Призыв купить', 'evidence':'Купите прямо сейчас по скидке'}))
    monkeypatch.setattr(pipeline.gemini, 'generate', AsyncMock(return_value=NEWS))
    result = await pipeline.process(NEWS, quality='fast', instructions='', lang='ru', api_key='test')
    assert result.ok and result.needs_review and not result.is_ad


@pytest.mark.asyncio
async def test_confirmed_offer_is_archived(monkeypatch):
    text = 'Рекламодатель ООО Цветы. Закажите букет со скидкой 20 процентов. Доставка сегодня по всему городу.'
    monkeypatch.setattr(pipeline.gemini, 'generate_json', AsyncMock(return_value={
        'decision':'ad', 'reason':'Предложение заказать букет', 'evidence':'Закажите букет со скидкой 20 процентов.'}))
    result = await pipeline.process(text, quality='fast', instructions='', lang='ru', api_key='test')
    assert not result.ok and result.is_ad
    assert 'Закажите букет' in result.ad_reasons[1]


@pytest.mark.asyncio
async def test_uncertain_material_does_not_autopublish_or_digest(store, channel, monkeypatch):
    await store.execute('UPDATE channels SET autopost = 1, digest_enabled = 1 WHERE id = ?', (channel['id'],))
    channel.update(autopost=1, digest_enabled=1)
    post_id = await store.insert(
        "INSERT INTO posts (channel_id, uid, raw_text, status, created_at) VALUES (?, 'review', ?, 'new', ?)",
        (channel['id'], NEWS, store.utcnow()))
    post = await store.fetch_one('SELECT * FROM posts WHERE id = ?', (post_id,))
    monkeypatch.setattr(pipeline, 'process', AsyncMock(return_value=pipeline.Result(True, text=NEWS, needs_review=True)))
    from app.bot import cards
    send = AsyncMock()
    monkeypatch.setattr(cards, 'send_moderation_card', send)
    await scheduler.process_post(AsyncMock(), post, channel)
    saved = await store.fetch_one('SELECT * FROM posts WHERE id = ?', (post_id,))
    assert saved['status'] == 'pending'
    assert 'ручная проверка' in saved['reason']
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_false_positive_archived_and_restorable(store, channel):
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id = 1')
    post = dict(channel_id=channel['id'], raw_text=NEWS)
    ad_id = await adbook.record(post, 7, ['old heuristic'])
    await set_ad_status(ad_id, 'false_positive', user)
    assert not await list_ads(channel['id'], user)
    archived = await list_ads(channel['id'], user, 'archive')
    assert archived[0]['status'] == 'false_positive'
    assert await adbook.record(post, 7, []) == ad_id
    assert not await list_ads(channel['id'], user)
    await set_ad_status(ad_id, 'new', user)
    assert (await list_ads(channel['id'], user))[0]['id'] == ad_id


@pytest.mark.asyncio
async def test_regen_uncertainty_revokes_scheduled_send(store, channel):
    from app.core.publisher import replace_draft
    from tests.test_pipeline_db import make_post
    post = await make_post(store, channel)
    assert await replace_draft(post, 'Переписано', None, needs_review=True)
    saved = await store.fetch_one('SELECT * FROM posts WHERE id = ?', (post['id'],))
    assert saved['status'] == 'pending'
