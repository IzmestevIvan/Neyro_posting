from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from app.bot import handlers, broadcasts


def message(user_id=1, text='/start'):
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id,username='client',first_name='Client'),
        chat=SimpleNamespace(type='private'),text=text,answer=AsyncMock(),answer_photo=AsyncMock())


@pytest.mark.asyncio
async def test_welcome_photo_and_menu_for_expired_user(store, owner, monkeypatch):
    monkeypatch.setattr(handlers,'PUBLIC_URL','https://app.example.org')
    await store.set_kv('support_username','Neyro_SupportBot')
    await store.execute("UPDATE users SET access_until=now()-interval '1 day' WHERE tg_id=1")
    msg=message()
    await handlers.cmd_start(msg)
    msg.answer_photo.assert_awaited_once()
    kwargs=msg.answer_photo.call_args.kwargs
    assert 'промокод' in kwargs['caption']
    buttons=[b for row in kwargs['reply_markup'].inline_keyboard for b in row]
    assert {b.text for b in buttons} == {'Открыть приложение','О продукте','Как начать','Поддержка'}
    assert next(b for b in buttons if b.text=='Поддержка').url=='https://t.me/Neyro_SupportBot'


@pytest.mark.asyncio
async def test_welcome_image_failure_has_text_fallback(store,owner):
    msg=message(); msg.answer_photo.side_effect=OSError('missing')
    await handlers.cmd_start(msg)
    msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_source_deeplink_is_preserved(store,owner):
    msg=message(text='/start add_source')
    await handlers.cmd_start(msg)
    msg.answer_photo.assert_not_called()
    assert 'Перешлите' in msg.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_public_menu_bypasses_expired_access(store, owner):
    from aiogram.types import CallbackQuery, User
    event=CallbackQuery(id='1',from_user=User(id=1,is_bot=False,first_name='X'),chat_instance='x',data='menu:about')
    handler=AsyncMock()
    await handlers.AccessMiddleware()(handler,event,{})
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_nonadmin_cannot_create(store,owner,monkeypatch):
    monkeypatch.setattr(broadcasts,'ADMIN_IDS',set())
    await broadcasts.prepare(message(text='/broadcast News'))
    assert (await store.fetch_one('SELECT count(*) AS n FROM broadcasts'))['n']==0


async def prepare_broadcast(store):
    await store.execute('UPDATE users SET is_admin=1 WHERE tg_id=1')
    await store.execute("INSERT INTO users(tg_id,created_at) VALUES(2,now()),(3,now())")
    await store.execute('UPDATE users SET blocked=1 WHERE tg_id=3')
    msg=message(text='/broadcast <b>Важное сообщение</b>')
    await broadcasts.prepare(msg)
    row=await store.fetch_one('SELECT * FROM broadcasts')
    assert msg.answer.call_args_list[1].kwargs['parse_mode'] is None
    return row


def confirmation(token,user_id=1, action='send'):
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id),data=f'broadcast:{action}:{token}',
        message=SimpleNamespace(chat=SimpleNamespace(type='private'),answer=AsyncMock(),edit_reply_markup=AsyncMock()),answer=AsyncMock())


@pytest.mark.asyncio
async def test_broadcast_preview_confirm_and_send_once(store,owner):
    row=await prepare_broadcast(store)
    bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)))
    assert not await broadcasts.deliver_one(bot)
    assert (await store.fetch_one('SELECT count(*) AS n FROM broadcast_recipients'))['n']==2
    cb=confirmation(row['id'])
    await broadcasts.confirm(cb)
    await broadcasts.confirm(cb)
    for _ in range(4): await broadcasts.deliver_one(bot)
    assert bot.send_message.await_count==2
    assert {c.args[0] for c in bot.send_message.call_args_list}=={1,2}
    assert all(c.kwargs['parse_mode'] is None for c in bot.send_message.call_args_list)
    assert (await store.fetch_one('SELECT status FROM broadcasts'))['status']=='done'


@pytest.mark.asyncio
async def test_broadcast_cancel_and_foreign_confirmation(store,owner,monkeypatch):
    monkeypatch.setattr(broadcasts,'ADMIN_IDS',set())
    row=await prepare_broadcast(store)
    await broadcasts.confirm(confirmation(row['id'],user_id=2))
    assert (await store.fetch_one('SELECT status FROM broadcasts'))['status']=='draft'
    await broadcasts.confirm(confirmation(row['id'],action='cancel'))
    await broadcasts.confirm(confirmation(row['id']))
    assert (await store.fetch_one('SELECT status FROM broadcasts'))['status']=='cancelled'


@pytest.mark.asyncio
async def test_broadcast_uncertain_not_retried_and_blocked_recipient_skipped(store,owner):
    row=await prepare_broadcast(store)
    await broadcasts.confirm(confirmation(row['id']))
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
    await broadcasts.deliver_one(bot)
    await store.execute('UPDATE users SET blocked=1 WHERE tg_id=2')
    await broadcasts.deliver_one(bot)
    await broadcasts.deliver_one(bot)
    assert bot.send_message.await_count==1
    states=await store.fetch_all('SELECT status FROM broadcast_recipients ORDER BY user_id')
    assert [s['status'] for s in states]==['uncertain','failed']


@pytest.mark.asyncio
async def test_broadcast_expired_confirmation(store,owner):
    row=await prepare_broadcast(store)
    await store.execute("UPDATE broadcasts SET created_at=now()-interval '31 minutes'")
    await broadcasts.confirm(confirmation(row['id']))
    assert (await store.fetch_one('SELECT status FROM broadcasts'))['status']=='draft'


@pytest.mark.asyncio
async def test_broadcast_restart_does_not_resend_claimed_recipient(store,owner):
    row=await prepare_broadcast(store)
    await broadcasts.confirm(confirmation(row['id']))
    await store.execute("UPDATE broadcast_recipients SET status='sending' WHERE user_id=1")
    await broadcasts.recover_interrupted()
    bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=9)))
    await broadcasts.deliver_one(bot)
    await broadcasts.deliver_one(bot)
    assert [c.args[0] for c in bot.send_message.call_args_list]==[2]
    msg=message(text='/broadcast_status')
    await broadcasts.status(msg)
    assert 'требуют сверки: 1' in msg.answer.call_args.args[0]
