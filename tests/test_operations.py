import logging
from unittest.mock import AsyncMock

import pytest

from app.core.operations import ErrorJournal, redact, channel_scope


def event(text='Ошибка источника %s', arg='news'):
    return logging.LogRecord('source', logging.CRITICAL, __file__, 12, text, (arg,), None)


@pytest.mark.asyncio
async def test_logs_every_repeat_and_groups_alerts(tmp_path):
    journal = ErrorJournal(tmp_path/'errors.log', admins={1})
    bot = AsyncMock()
    try:
        for _ in range(7):
            journal.handle(event())
        await journal.flush_alerts(bot)
        assert bot.send_message.await_count == 1
        assert 'Событий: 7' in bot.send_message.call_args.args[1]
        assert (tmp_path/'errors.log').read_text().count('Ошибка источника') == 7
        journal.handle(event())
        await journal.flush_alerts(bot)
        assert bot.send_message.await_count == 1
        journal.last_sent.clear()
        await journal.flush_alerts(bot)
        assert bot.send_message.await_count == 2
        assert not journal.pending
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_warning_is_panel_only_and_delivery_is_emergency(tmp_path):
    from app.core.operations import recent_events
    journal = ErrorJournal(tmp_path/'errors.log', admins={1})
    bot = AsyncMock()
    try:
        warning = logging.LogRecord('ai', logging.WARNING, __file__, 1, 'HTTP 429', (), None)
        journal.handle(warning)
        await journal.flush_alerts(bot)
        bot.send_message.assert_not_awaited()
        events = recent_events(tmp_path/'events.jsonl')
        assert len(events) == 1 and not events[0]['emergency']
        journal.handle(logging.LogRecord('operations.delivery', logging.ERROR, __file__, 2, 'Прерванная доставка', (), None))
        await journal.flush_alerts(bot)
        bot.send_message.assert_awaited_once()
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_failed_notification_retained_and_concurrent_errors_not_lost(tmp_path):
    journal = ErrorJournal(tmp_path/'errors.log', admins={1})
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError('offline')
    try:
        journal.handle(event())
        await journal.flush_alerts(bot)
        assert journal.pending
        async def send(*args, **kwargs):
            journal.handle(event())
        bot.send_message.side_effect = send
        await journal.flush_alerts(bot)
        assert next(iter(journal.pending.values()))['count'] == 1
    finally:
        journal.close()


def test_redaction_and_bounded_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'private-api-secret')
    journal = ErrorJournal(tmp_path/'errors.log', admins=set())
    journal.disk.maxBytes = 250
    try:
        for _ in range(20):
            journal.handle(event('%s', 'private-api-secret https://example.com/?key=hidden'))
        files = list(tmp_path.glob('errors.log*'))
        assert len(files) <= 5
        assert all('private-api-secret' not in p.read_text() and 'key=hidden' not in p.read_text() for p in files)
        assert 'password' not in redact('postgresql://user:password@server/db')
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_alert_names_channel_and_does_not_hide_different_provider_errors(tmp_path):
    journal = ErrorJournal(tmp_path/'errors.log', admins={1})
    bot = AsyncMock()
    try:
        with channel_scope({'id':4, 'owner_id':8032781999, 'title':'Тест <канал>'}):
            journal.handle(event('%s', 'gemini-3.6-flash: HTTP 429'))
            journal.handle(event('%s', 'gemini-3.1-flash-lite: HTTP 503'))
        assert len(journal.pending) == 2
        await journal.flush_alerts(bot)
        text = bot.send_message.call_args.args[1]
        assert 'Тест &lt;канал&gt;' in text and '8032781999' in text
        assert 'ограничение запросов' in text and 'Сбой на стороне провайдера' in text
        assert 'МСК' in text and 'UTC' not in text and 'HTTP' not in text
        assert 'data/logs' not in text
    finally:
        journal.close()
