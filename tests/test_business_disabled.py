"""The suspended feature must remain inert without touching the database or providers."""
import asyncio
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app import db
from app.api import routes
from app.bot import handlers
from app.core import business, features, publisher, scheduler


@pytest.fixture(autouse=True)
def disabled_business(monkeypatch, request):
    monkeypatch.setattr(features, 'BUSINESS_MODE_ENABLED', False)
    if 'store' not in request.fixturenames:
        for name in ('connect', 'fetch_one', 'fetch_all', 'execute', 'update', 'insert', 'get_kv', 'set_kv', 'bump_stat'):
            monkeypatch.setattr(db, name, AsyncMock(side_effect=AssertionError(f'Unexpected database call: {name}')))
    for name in ('generate', 'generate_json'):
        monkeypatch.setattr(business.gemini, name, AsyncMock(side_effect=AssertionError('Unexpected AI call')))


@pytest.mark.parametrize('configured, expected', [(None, False), ('', False), ('false', False), ('0', False),
                                                ('unexpected', False), ('TRUE', True), ('1', True)])
def test_business_flag_requires_explicit_opt_in(monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv('BUSINESS_MODE_ENABLED', raising=False)
    else:
        monkeypatch.setenv('BUSINESS_MODE_ENABLED', configured)
    spec = importlib.util.spec_from_file_location('_isolated_features', features.__file__)
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    assert isolated.BUSINESS_MODE_ENABLED is expected


@pytest.mark.parametrize('operation', ['generate', 'draft_text', 'research_company'])
async def test_disabled_editor_rejects_before_any_work(operation):
    args = {'generate': (1,), 'draft_text': ({},), 'research_company': ({}, {})}
    with pytest.raises(ValueError, match='временно недоступен'):
        await getattr(business, operation)(*args[operation])


async def test_disabled_automatic_editor_never_reads_or_reschedules():
    await business.propose_due()
    await business.proposal_loop()


@pytest.mark.parametrize('field', ['business_mode', 'business_draft', 'business_generated'])
async def test_suspended_processing_preserves_posts_without_news_fallback(monkeypatch, field):
    channel, post = {'id': 1}, {'id': 2}
    (channel if field == 'business_mode' else post)[field] = 1
    before = (dict(channel), dict(post))
    process = AsyncMock(side_effect=AssertionError('News processing must not run'))
    monkeypatch.setattr(scheduler.pipeline, 'process', process)
    await scheduler.process_post(AsyncMock(), post, channel)
    await scheduler._process_post(AsyncMock(), post, channel)
    assert (channel, post) == before


async def test_processing_rechecks_database_business_mode(monkeypatch):
    channel = {'id': 1, 'owner_id': 3, 'business_mode': 0}
    post = {'id': 2, 'is_manual': 1}
    monkeypatch.setattr(scheduler.access, 'owner_has_access', AsyncMock(return_value=True))
    monkeypatch.setattr(db, 'fetch_one', AsyncMock(side_effect=[post, dict(channel, business_mode=1)]))
    process = AsyncMock()
    monkeypatch.setattr(scheduler, '_process_post', process)
    await scheduler.process_post(AsyncMock(), post, channel)
    process.assert_not_awaited()


async def test_news_processing_still_dispatches(monkeypatch):
    channel = {'id': 1, 'owner_id': 3, 'business_mode': 0}
    post = {'id': 2, 'is_manual': 1}
    monkeypatch.setattr(scheduler.access, 'owner_has_access', AsyncMock(return_value=True))
    monkeypatch.setattr(db, 'fetch_one', AsyncMock(side_effect=[post, channel]))
    process = AsyncMock()
    monkeypatch.setattr(scheduler, '_process_post', process)
    bot = AsyncMock()
    await scheduler.process_post(bot, post, channel)
    process.assert_awaited_once_with(bot, post, channel, force_once=False)


async def test_disabled_start_only_launches_news_workers(monkeypatch):
    proposal = AsyncMock()
    monkeypatch.setattr(business, 'proposal_loop', proposal)
    workers = []
    for name in ('poll_loop', 'process_loop', 'publish_loop', 'digest_loop', 'stats_loop'):
        worker = AsyncMock()
        workers.append(worker)
        monkeypatch.setattr(scheduler, name, worker)
    tasks = scheduler.start(AsyncMock())
    await asyncio.gather(*tasks)
    assert len(tasks) == 5
    proposal.assert_not_called()
    for worker in workers:
        worker.assert_awaited_once()


async def test_business_channel_never_polls_news_or_publishes_fresh(monkeypatch):
    channel = {'id': 1, 'business_mode': 1}
    fetch = AsyncMock(side_effect=AssertionError('No source fetch expected'))
    monkeypatch.setattr(scheduler, 'fetch_source', fetch)
    assert await scheduler.poll_source(AsyncMock(), channel, {}) == 0
    await scheduler.run_digest(AsyncMock(), channel)
    with pytest.raises(ValueError, match='временно недоступен'):
        await scheduler.publish_fresh_once(AsyncMock(), channel)


async def test_manual_bot_entry_does_not_generate_or_save(monkeypatch):
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), reply_to_message=None,
                              text='Новый материал', caption=None, answer=AsyncMock())
    channel = {'id': 1, 'business_mode': 1}
    monkeypatch.setattr(handlers, 'ensure_user', AsyncMock())
    monkeypatch.setattr(handlers, 'active_channel', AsyncMock(return_value=channel))
    build = AsyncMock(side_effect=AssertionError('Must not read external material'))
    monkeypatch.setattr(handlers, '_build_manual_post', build)
    await handlers.on_manual(message, AsyncMock())
    message.answer.assert_awaited_once_with(features.BUSINESS_MODE_UNAVAILABLE)


@pytest.mark.parametrize('action', ['approve', 'regen', 'reject'])
@pytest.mark.parametrize('business_channel', [True, False])
async def test_legacy_bot_buttons_cannot_mutate_company_data(monkeypatch, action, business_channel):
    post = {'id': 2, 'channel_id': 1, 'owner_id': 3, 'business_draft': int(not business_channel)}
    channel = {'id': 1, 'business_mode': int(business_channel)}
    monkeypatch.setattr(db, 'fetch_one', AsyncMock(side_effect=[post, channel]))
    callback = SimpleNamespace(data=f'mod:{action}:2', from_user=SimpleNamespace(id=3), answer=AsyncMock())
    await handlers.on_moderation(callback, AsyncMock())
    callback.answer.assert_awaited_once_with(features.BUSINESS_MODE_UNAVAILABLE, show_alert=True)


async def test_legacy_bot_edit_cannot_rewrite_company_draft(monkeypatch):
    post = {'id': 2, 'channel_id': 1, 'business_generated': 1}
    monkeypatch.setattr(db, 'fetch_one', AsyncMock(side_effect=[post, {'id': 1, 'business_mode': 0}]))
    message = SimpleNamespace(reply_to_message=SimpleNamespace(message_id=9),
                              from_user=SimpleNamespace(id=3), reply=AsyncMock())
    await handlers.on_edit_reply(message, AsyncMock())
    message.reply.assert_awaited_once_with(features.BUSINESS_MODE_UNAVAILABLE)


@pytest.mark.parametrize('field', ['business_mode', 'business_draft', 'business_generated'])
async def test_maintenance_preserves_company_history_and_keeps_news_working(store, channel, field):
    from tests.test_pipeline_db import make_post

    other_id = await store.insert(
        'INSERT INTO channels (owner_id,created_at) VALUES (?,now())', (channel['owner_id'],))
    protected_channel = await store.fetch_one('SELECT * FROM channels WHERE id=?', (other_id,))
    protected = await make_post(store, protected_channel, status='filtered')
    normal = await make_post(store, channel, status='filtered')
    await store.execute("UPDATE posts SET reason='нет медиа'")
    if field == 'business_mode':
        await store.execute('UPDATE channels SET business_mode=1 WHERE id=?', (other_id,))
    else:
        await store.execute(f'UPDATE posts SET {field}=1 WHERE id=?', (protected['id'],))
    before = await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],))
    assert await scheduler.reconsider_filtered(other_id) == 0
    assert await scheduler.reconsider_filtered(channel['id']) == 1
    assert await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],)) == before

    await store.execute("UPDATE posts SET status='approved',created_at=now()-interval '2 hours'")
    before = await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],))
    assert await scheduler.expire_news() == 1
    assert await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],)) == before
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (normal['id'],)))['status'] == 'expired'

    await store.execute("UPDATE posts SET status='rejected',created_at=now()-interval '100 days'")
    before = await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],))
    await scheduler.prune_posts()
    assert await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],)) == before
    assert await store.fetch_one('SELECT * FROM posts WHERE id=?', (normal['id'],)) is None


@pytest.mark.parametrize('key', ['business_mode', 'business_auto', 'business_profile'])
def test_business_settings_cannot_change_saved_state(key):
    with pytest.raises(HTTPException) as error:
        routes._clean_settings({key: False if key != 'business_profile' else {}})
    assert error.value.status_code == 409
    assert error.value.detail == features.BUSINESS_MODE_UNAVAILABLE


@pytest.mark.parametrize('field', ['business_mode', 'business_draft', 'business_generated'])
@pytest.mark.parametrize('action', ['approve', 'regen', 'reject', 'edit', 'photo', 'remove_photo', 'confirm_sent', 'confirm_absent'])
async def test_api_post_actions_reject_before_body_or_providers(monkeypatch, field, action):
    channel, post = {'id': 1}, {'id': 2, 'channel_id': 1}
    (channel if field == 'business_mode' else post)[field] = 1
    monkeypatch.setattr(db, 'fetch_one', AsyncMock(return_value=post))
    monkeypatch.setattr(routes, 'owned_channel', AsyncMock(return_value=channel))
    with pytest.raises(HTTPException) as error:
        await routes.post_action(2, action, SimpleNamespace(), {'tg_id': 3})
    assert error.value.status_code == 409
    assert error.value.detail == features.BUSINESS_MODE_UNAVAILABLE


@pytest.mark.parametrize('operation', ['delete_channel', 'update_channel', 'upload_logo', 'publish_now', 'add_source', 'copy_channel_sources', 'business_draft'])
async def test_api_company_operations_are_read_only(monkeypatch, operation):
    monkeypatch.setattr(routes, 'owned_channel', AsyncMock(return_value={'id': 1, 'business_mode': 1}))
    user, request = {'tg_id': 3}, SimpleNamespace()
    args = {'delete_channel': (1, user), 'update_channel': (1, {'autopost': True}, user),
            'upload_logo': (1, request, user), 'publish_now': (1, request, user),
            'add_source': (1, {'ref': 'example'}, user),
            'copy_channel_sources': (1, {'from_channel_id': 2, 'source_ids': [4]}, user),
            'business_draft': (1, {}, user)}
    with pytest.raises(HTTPException) as error:
        await getattr(routes, operation)(*args[operation])
    assert error.value.status_code == 409
    assert error.value.detail == features.BUSINESS_MODE_UNAVAILABLE


@pytest.mark.parametrize('field', ['business_mode', 'business_draft', 'business_generated'])
async def test_publisher_entry_rejects_before_access_or_database(field):
    channel, post = {'id': 1}, {'id': 2}
    (channel if field == 'business_mode' else post)[field] = 1
    for operation in (publisher.publish_post, publisher._publish_post):
        with pytest.raises(publisher.AlreadyPublished, match='временно недоступен'):
            await operation(AsyncMock(), post, channel, approved_by_user=True)


@pytest.mark.parametrize('field', ['business_mode', 'business_draft', 'business_generated'])
async def test_publisher_rechecks_saved_state_and_preserves_company_post(store, channel, field):
    from tests.test_pipeline_db import FakeBot, make_post

    stale = await make_post(store, channel, status='pending')
    if field == 'business_mode':
        await store.execute('UPDATE channels SET business_mode=1 WHERE id=?', (channel['id'],))
    else:
        await store.execute(f'UPDATE posts SET {field}=1 WHERE id=?', (stale['id'],))
    before = await store.fetch_one('SELECT * FROM posts WHERE id=?', (stale['id'],))
    bot = FakeBot()
    with pytest.raises(publisher.AlreadyPublished, match='временно недоступен'):
        await publisher.publish_post(bot, stale, channel, approved_by_user=True, approved_text=stale['text_out'])
    assert not await publisher.reject_post(stale['id'])
    assert not await publisher.replace_draft(stale, 'Changed draft text', None)
    assert await store.fetch_one('SELECT * FROM posts WHERE id=?', (stale['id'],)) == before
    assert not await store.fetch_all('SELECT * FROM delivery_attempts')
    assert not bot.sent
    await store.execute("UPDATE posts SET status='uncertain' WHERE id=?", (stale['id'],))
    before = await store.fetch_one('SELECT * FROM posts WHERE id=?', (stale['id'],))
    for delivered in (True, False):
        with pytest.raises(publisher.AlreadyPublished, match='временно недоступен'):
            await publisher.reconcile_delivery(stale['id'], channel, delivered=delivered)
        assert await store.fetch_one('SELECT * FROM posts WHERE id=?', (stale['id'],)) == before


@pytest.mark.parametrize('field', ['business_mode', 'business_draft', 'business_generated'])
@pytest.mark.parametrize('has_attempt', [False, True])
async def test_delivery_recovery_preserves_company_records(store, channel, field, has_attempt):
    from tests.test_pipeline_db import make_post

    other_id = await store.insert('INSERT INTO channels(owner_id,created_at) VALUES(?,now())', (channel['owner_id'],))
    company = await store.fetch_one('SELECT * FROM channels WHERE id=?', (other_id,))
    protected = await make_post(store, company, status='publishing')
    ordinary = await make_post(store, channel, status='publishing')
    if field == 'business_mode':
        await store.execute('UPDATE channels SET business_mode=1 WHERE id=?', (other_id,))
    else:
        await store.execute(f'UPDATE posts SET {field}=1 WHERE id=?', (protected['id'],))
    await store.execute("UPDATE posts SET created_at=now()-interval '20 minutes'")
    if has_attempt:
        for post in (protected, ordinary):
            await store.execute(
                "INSERT INTO delivery_attempts(post_id,channel_id,owner_id,worker_id,started_at) "
                "VALUES(?,?,?,'stopped-worker',now()-interval '20 minutes')",
                (post['id'], post['channel_id'], channel['owner_id']))
    before = await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],))
    attempt_before = await store.fetch_all('SELECT * FROM delivery_attempts WHERE post_id=?', (protected['id'],))
    assert await publisher.recover_stale_deliveries() == 1
    assert await store.fetch_one('SELECT * FROM posts WHERE id=?', (protected['id'],)) == before
    assert await store.fetch_all('SELECT * FROM delivery_attempts WHERE post_id=?', (protected['id'],)) == attempt_before
    assert (await store.fetch_one('SELECT status FROM posts WHERE id=?', (ordinary['id'],)))['status'] == 'uncertain'


async def test_bootstrap_and_feeds_expose_read_only_company_state(store, channel):
    from tests.test_pipeline_db import make_post

    await store.execute('UPDATE channels SET business_mode=1 WHERE id=?', (channel['id'],))
    await make_post(store, channel, status='pending')
    await make_post(store, channel, status='published')
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=?', (channel['owner_id'],))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot_username='testbot')))
    result = await routes.bootstrap(request, user)
    assert result['features']['business_mode'] is False
    assert result['channels'][0]['business_mode'] == 1
    assert result['channels'][0]['business_unavailable'] is True
    assert (await routes.channel_feed(channel['id'], user))[0]['business_unavailable'] is True
    assert (await routes.channel_history(channel['id'], user))[0]['business_unavailable'] is True


async def test_source_copy_remains_available_for_news_destination(store, channel):
    origin = await store.insert('INSERT INTO channels(owner_id,business_mode,created_at) VALUES(?,1,now())',
                                (channel['owner_id'],))
    source = await store.insert(
        "INSERT INTO sources(channel_id,kind,ref,title,created_at) VALUES(?,'tg','example','Example',now())", (origin,))
    user = await store.fetch_one('SELECT * FROM users WHERE tg_id=?', (channel['owner_id'],))
    result = await routes.copy_channel_sources(channel['id'], {'from_channel_id': origin, 'source_ids': [source]}, user)
    assert result == {'added': 1, 'skipped': 0}
    assert (await store.fetch_one('SELECT business_mode FROM channels WHERE id=?', (origin,)))['business_mode'] == 1
    assert len(await store.fetch_all('SELECT * FROM sources WHERE channel_id=?', (origin,))) == 1
