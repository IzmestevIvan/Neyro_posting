from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import support


def message(user=10, reply=None, text='Нужна помощь'):
    return SimpleNamespace(from_user=SimpleNamespace(id=user, is_bot=False, full_name='Client'),
                           message_id=90, reply_to_message=reply, text=text, answer=AsyncMock())


@pytest.mark.asyncio
async def test_support_routes_and_admin_reply(store, monkeypatch):
    monkeypatch.setattr(support, 'ADMIN_IDS', {1})
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=100)
    bot.copy_message.return_value = SimpleNamespace(message_id=101)
    user = message()
    await support.handle_message(user, bot)
    assert 'передано' in user.answer.call_args.args[0]
    row = await store.fetch_one('SELECT user_id FROM support_routes WHERE admin_id=1 AND message_id=101')
    assert row['user_id'] == 10
    admin = message(1, SimpleNamespace(message_id=101), 'Ответ поддержки')
    await support.handle_message(admin, bot)
    assert bot.copy_message.call_args.args == (10,1,90)
    assert 'отправлен' in admin.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_admin_cannot_answer_unmapped_or_other_admin_message(store, monkeypatch):
    monkeypatch.setattr(support, 'ADMIN_IDS', {1,2})
    await store.execute('INSERT INTO support_routes(admin_id,message_id,user_id) VALUES(2,101,10)')
    bot = AsyncMock()
    admin = message(1, SimpleNamespace(message_id=101))
    await support.handle_message(admin, bot)
    bot.copy_message.assert_not_awaited()
    assert 'не найдено' in admin.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_failed_support_delivery_not_claimed_success(store, monkeypatch):
    monkeypatch.setattr(support, 'ADMIN_IDS', {1})
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError('blocked')
    user = message()
    await support.handle_message(user, bot)
    assert 'Не удалось' in user.answer.call_args.args[0]
