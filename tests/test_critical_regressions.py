import asyncio
from datetime import timedelta

import pytest

from app.core import promo
from tests.db_safety import validate_test_url


@pytest.mark.parametrize('url', [
    'postgresql://u:p@localhost/prod',
    'postgresql://u:p@remote/neyro_test',
    'postgresql://u:p@localhost/neyro_test?options=-csearch_path=public',
    'postgresql://u:p@localhost/neyro',
])
def test_destructive_fixture_rejects_unsafe_database(url):
    with pytest.raises(ValueError):
        validate_test_url(url)


def test_test_database_allowed():
    assert validate_test_url('postgresql://u:p@127.0.0.1/neyro_test')


@pytest.mark.asyncio
async def test_concurrent_redemption_has_one_recipient(store, owner):
    await store.execute('INSERT INTO users (tg_id, created_at) VALUES (2, ?)', (store.utcnow(),))
    code, = await promo.create_codes(1)
    outcomes = await asyncio.gather(promo.redeem(code, owner), promo.redeem(code, 2), return_exceptions=True)
    assert sum(isinstance(x, dict) for x in outcomes) == 1
    assert sum(isinstance(x, promo.PromoError) for x in outcomes) == 1
    users = await store.fetch_all('SELECT * FROM users WHERE access_until IS NOT NULL')
    assert len(users) == 1
    used = await store.fetch_one('SELECT used_by FROM promo_codes WHERE code = ?', (code,))
    assert used['used_by'] == users[0]['tg_id']


@pytest.mark.asyncio
async def test_new_codes_extend_serially_and_replay_does_not_downgrade(store, owner):
    codes = await promo.create_codes(2, plan='pro')
    before = store.utcnow()
    await asyncio.gather(*(promo.redeem(code, owner) for code in codes))
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id = ?', (owner,))
    assert user['access_until'] >= before + timedelta(days=60)
    await store.execute('UPDATE users SET daily_limit=999 WHERE tg_id=?', (owner,))
    result = await promo.redeem(codes[0], owner)
    assert result['access_until'] == user['access_until']
    assert result['daily_limit'] == 999


@pytest.mark.asyncio
async def test_code_suffix_is_not_ignored(store, owner):
    code, = await promo.create_codes(1)
    for malformed in (code+'MORE', code+'!', code.replace('-', '/')):
        with pytest.raises(promo.PromoError):
            await promo.redeem(malformed, owner)

@pytest.mark.asyncio
@pytest.mark.parametrize('check', [{}, {'ok': 'true'}, {'ok': True},
    {'ok': True, 'hallucinations': ['invented'], 'distortions': [], 'verdict': 'ok'}])
async def test_malformed_factcheck_blocks_publication(monkeypatch, check):
    from app.ai import pipeline, gemini
    async def generate_json(prompt, **kwargs):
        if kwargs.get('allow_fallback') is False:
            return check
        return {'is_ad': False, 'is_offtopic': False, 'is_newsworthy': True}
    async def generate(*args, **kwargs):
        return 'Переписанная новость, которую пока нельзя считать проверенной.'
    monkeypatch.setattr(gemini, 'generate_json', generate_json)
    monkeypatch.setattr(gemini, 'generate', generate)
    result = await pipeline.process('В городе сегодня открыли новый общественный парк с пешеходными дорожками.',
        quality='super', instructions='', lang='ru', api_key='test')
    assert not result.ok
    assert 'фактчек' in result.reason


@pytest.mark.asyncio
async def test_post_never_matches_its_own_fingerprint(store, channel):
    from app.core import scheduler
    await store.execute("INSERT INTO posts (channel_id, status, fingerprint, created_at) VALUES (?, 'new', 'same words', ?)",
                        (channel['id'], store.utcnow()))
    post = await store.fetch_one('SELECT * FROM posts WHERE channel_id=?', (channel['id'],))
    assert await scheduler._known_fingerprints(channel['id'], post['id']) == []

@pytest.mark.asyncio
@pytest.mark.parametrize('role,can_post,allowed', [('member', True, False), ('left', True, False),
    ('administrator', False, False), ('creator', True, True)])
async def test_channel_permissions_require_user_and_bot_rights(role, can_post, allowed):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.core.publisher import verify_channel_permissions
    bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=99)),
        get_chat_member=AsyncMock(side_effect=[SimpleNamespace(status=role),
            SimpleNamespace(status='administrator', can_post_messages=can_post)]))
    if allowed:
        await verify_channel_permissions(bot, -100, 1)
    else:
        with pytest.raises(PermissionError):
            await verify_channel_permissions(bot, -100, 1)

@pytest.mark.asyncio
async def test_failed_raw_post_cannot_be_sent(store, channel):
    from app.core import publisher
    from tests.test_pipeline_db import FakeBot
    post_id = await store.insert("INSERT INTO posts (channel_id, status, raw_text, created_at) VALUES (?, 'failed', 'raw', ?)",
                                (channel['id'], store.utcnow()))
    post = await store.fetch_one('SELECT * FROM posts WHERE id=?', (post_id,))
    bot = FakeBot()
    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot, post, channel)
    assert not bot.sent


@pytest.mark.asyncio
async def test_sent_post_cannot_be_rejected_or_rewritten(store, channel):
    from app.core import publisher
    from tests.test_pipeline_db import FakeBot, make_post
    post = await make_post(store, channel)
    await publisher.publish_post(FakeBot(), post, channel)
    assert not await publisher.reject_post(post['id'])
    assert not await publisher.replace_draft(post, 'new text', None)
    fresh = await store.fetch_one('SELECT * FROM posts WHERE id=?', (post['id'],))
    assert fresh['status'] == 'published'
    assert fresh['text_out'] == post['text_out']


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked', [False, True])
async def test_closed_owner_cannot_publish(store, channel, blocked):
    from app.core import publisher
    from tests.test_pipeline_db import FakeBot, make_post
    post = await make_post(store, channel)
    await store.execute("UPDATE users SET blocked=?, access_until=now()-interval '1 day' WHERE tg_id=?",
                        (int(blocked), channel['owner_id']))
    bot = FakeBot()
    with pytest.raises(PermissionError):
        await publisher.publish_post(bot, post, channel)
    assert not bot.sent

@pytest.mark.asyncio
async def test_source_cursor_and_posts_rollback_together(store, channel, monkeypatch):
    from app.core import scheduler
    from app.sources.telegram_web import RawItem
    sid = await store.insert("INSERT INTO sources (channel_id,kind,ref,last_uid,created_at) VALUES (?,'tg','donor','old',?)",
                             (channel['id'], store.utcnow()))
    source = await store.fetch_one('SELECT * FROM sources WHERE id=?', (sid,))
    async def fetch(*args):
        # Second insert cannot encode its media. The first INSERT must roll back too.
        return [RawItem('old','https://example.org/old','old'), RawItem('new','https://example.org/new','new'),
                RawItem('bad','https://example.org/bad','bad', media=[{'bad': object()}])], 'Donor'
    monkeypatch.setattr(scheduler.telegram_web, 'fetch', fetch)
    with pytest.raises(TypeError):
        await scheduler.poll_source(None, channel, source)
    assert not await store.fetch_all('SELECT * FROM posts')
    fresh = await store.fetch_one('SELECT * FROM sources WHERE id=?', (sid,))
    assert fresh['last_uid'] == 'old'


@pytest.mark.asyncio
async def test_api_refuses_channel_when_requester_is_not_admin(store, owner):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.api.routes import add_channel
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=?', (owner,))
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-555, title='Other', username='other')),
                          get_chat_member=AsyncMock(return_value=SimpleNamespace(status='member')))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)))
    with pytest.raises(HTTPException) as failure:
        await add_channel(request, {'ref': '@other'}, user)
    assert failure.value.status_code == 403
    assert not await store.fetch_all('SELECT * FROM channels')


@pytest.mark.asyncio
async def test_revoked_owner_does_not_reach_ai_or_sources(store, channel, monkeypatch):
    from unittest.mock import AsyncMock
    from app.core import scheduler
    await store.execute('UPDATE users SET blocked=1 WHERE tg_id=?', (channel['owner_id'],))
    fetch = AsyncMock()
    process = AsyncMock()
    monkeypatch.setattr(scheduler.telegram_web, 'fetch', fetch)
    monkeypatch.setattr(scheduler.pipeline, 'process', process)
    await scheduler.poll_channel(None, channel)
    await scheduler.process_post(None, {}, channel)
    await scheduler.run_digest(None, channel)
    fetch.assert_not_called()
    process.assert_not_called()


@pytest.mark.asyncio
async def test_dashboard_ready_excludes_raw_and_sent(store, channel):
    from app.api.routes import channel_stats
    for status, text in [('new', 'raw'), ('pending', 'ready'), ('failed', ''), ('published', 'sent')]:
        await store.execute(
            "INSERT INTO posts (channel_id, uid, raw_text, text_out, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (channel['id'], status, 'original', text, status, store.utcnow()),
        )
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id = 1')
    stats = await channel_stats(channel['id'], user)
    assert stats['ready'] == 1
    assert stats['system'] is None
