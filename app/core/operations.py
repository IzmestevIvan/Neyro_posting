"""Bounded, persistent error journal and grouped administrator notifications."""
import asyncio
import hashlib
import json
import html
import logging
import os
import re
import time
from collections import OrderedDict, deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import ADMIN_IDS, ROOT

EVENTS_PATH = ROOT / 'data/logs/events.jsonl'


def recent_events(path=None):
    path = path or EVENTS_PATH
    rows = []
    for candidate in (path.with_name(path.name + '.1'), path):
        if not candidate.exists():
            continue
        with candidate.open('rb') as stream:
            stream.seek(max(0, candidate.stat().st_size - 128 * 1024))
            for line in stream.read().decode('utf-8', errors='replace').splitlines():
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    pass
    return rows[-60:][::-1]

_channel = ContextVar('error_channel', default=None)


@contextmanager
def channel_scope(channel):
    token = _channel.set({'id': channel['id'], 'title': channel.get('title') or str(channel['id']),
                          'owner': channel.get('owner_id')})
    try:
        yield
    finally:
        _channel.reset(token)


def explain_error(detail, where):
    loops = {'operations.process-loop':'подготовки постов','operations.publish-loop':'отправки постов','operations.poll-loop':'чтения источников','operations.stats-loop':'сбора статистики','operations.support':'поддержки'}
    if where in loops:
        return f'Повторяются сбои {loops[where]}', 'Операция несколько раз завершилась ошибкой. Это не подтверждение остановки всех каналов. Проверьте прогресс в админ-панели; если он отсутствует, передайте журнал технической поддержке.'
    if where == 'operations.delivery':
        if 'Не отправлен' in detail:
            return 'Пост не отправлен', detail
        if 'часть' in detail:
            return 'Пост отправлен частично', detail
        if 'неизвестен' in detail:
            return 'Результат отправки неизвестен', detail
        return 'Нужна сверка отправки', 'Проверьте историю публикаций и сам канал. Не повторяйте отправку, пока не проверите, появился ли пост.'
    if 'TimeoutError' in detail and where == 'ai':
        return 'ИИ не ответил вовремя', 'Один запрос превысил время ожидания. Результат попытки через резерв смотрите в следующих событиях; это не подтверждение остановки приложения.'
    if 'Обработка продолжена:' in detail:
        return '✅ Резервный ИИ ответил', 'Запрос выполнен через резерв. Это ещё не означает публикацию: материал должен пройти остальные проверки и расписание.'
    if 'HTTP 429' in detail:
        account = 'Клиентский ключ' if '(client)' in detail else 'Ключ сервиса'
        return f'{account}: ограничение запросов', 'Провайдер отклонил запрос из-за квоты или частоты обращений. Пробуем доступный резерв. Если ошибка повторяется, проверьте квоту и оплату этого ключа.'
    if re.search(r'HTTP 5\d\d', detail):
        return 'ИИ временно недоступен', 'Сбой на стороне провайдера. Пробуем доступный резерв; если не ответит ни одна модель, материал останется в очереди.'
    if re.search(r'HTTP (?:400|401|403)', detail):
        return 'ИИ отклонил ключ или запрос', 'Проверьте действительность ключа и доступ к модели. Для клиентского ключа система пробует ключ сервиса.'
    if 'main model on cooldown' in detail:
        return 'Основная модель временно пропускается', 'Используются резервные модели. Это служебное переключение, не остановка всего приложения.'
    if 'Клиентский ключ недоступен' in detail:
        return 'Переключение на ключ сервиса', 'Ключ клиента не ответил. Пробуем наш ключ; результат обработки будет указан отдельно.'
    if where == 'operations.ai':
        return 'Материал пока не подготовлен', detail.replace('триаж', 'проверка тематики и рекламы').replace('фактчек', 'проверка фактов')
    if 'source' in detail.lower() or where == 'sources':
        return 'Не удалось прочитать источник', 'При следующем опросе будет повторная попытка. Проверьте доступность источника в приложении.'
    if where in ('publisher', 'operations.delivery'):
        return 'Нужна проверка публикации', 'Откройте историю канала и причину ошибки. Не отправляйте пост повторно, пока не проверите, появился ли он в Telegram.'
    if where == 'watermark':
        return 'Не удалось наложить логотип', 'Изображение может выйти без водяного знака. Проверьте PNG в настройках канала.'
    if where == 'api':
        return 'Запрос в приложении не выполнен', 'Обновите страницу и проверьте результат операции перед повтором. Если ошибка повторяется, нужно проверить журнал сервера.'
    if 'factcheck' in detail.lower():
        return 'Проверка фактов не завершена', 'Материал нельзя публиковать без завершения проверки. Проверьте доступность и квоту ИИ.'
    if re.search('[а-яА-Я]', detail):
        return 'Сбой в работе приложения', detail
    return 'Сбой в работе приложения', 'Операция завершилась с ошибкой. Проверьте состояние канала в приложении; техническая причина записана в журнале сервера.'


def redact(text):
    for name, value in os.environ.items():
        if len(value) >= 8 and any(word in name.upper() for word in ('TOKEN', 'KEY', 'PASSWORD', 'DATABASE_URL')):
            text = text.replace(value, '[secret]')
    text = re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}', '[bot-token]', text)
    text = re.sub(r'AIza[A-Za-z0-9_-]{20,}', '[api-key]', text)
    text = re.sub(r'AQ\.[A-Za-z0-9_.-]+', '[api-key]', text)
    text = re.sub(r'(?i)(https?://[^\s?]+)\?[^\s]+', r'\1?[redacted]', text)
    return re.sub(r'(?i)(postgres(?:ql)?://)[^\s@]+@', r'\1[credentials]@', text)


class SafeFormatter(logging.Formatter):
    def format(self, record):
        context = _channel.get()
        suffix = f" [channel={context['id']} owner={context['owner']}]" if context else ''
        return redact(super().format(record) + suffix)


class ErrorJournal(logging.Handler):
    def __init__(self, path: Path, admins=None):
        super().__init__(logging.WARNING)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.disk = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=4, encoding='utf-8')
        path.chmod(0o600)
        self.disk.setFormatter(SafeFormatter('%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s'))
        self.events = RotatingFileHandler(path.with_name('events.jsonl'), maxBytes=1024*1024, backupCount=2, encoding='utf-8')
        path.with_name('events.jsonl').chmod(0o600)
        self.events.setFormatter(logging.Formatter('%(message)s'))
        self.repeats = OrderedDict()
        self.pending = OrderedDict()
        self.last_sent = {}
        self.overflow = 0
        self.admins = set(ADMIN_IDS if admins is None else admins)
        self.sending = False

    def emit(self, record):
        # Disk receives every occurrence, even failures sending an alert itself.
        self.disk.emit(record)
        if record.name == 'operations.alert_transport':
            return
        context = _channel.get()
        # HTTP status/model must remain distinct: a later 429 cannot erase an earlier 503.
        dimensions = re.findall(r'HTTP \d+|gemini-[\w.-]+|\(client\)|\(service\)', record.getMessage())
        key = hashlib.sha256(f'{record.name}:{record.lineno}:{record.msg}:{dimensions}:{context}:{record.exc_info[0] if record.exc_info else ""}'.encode()).hexdigest()[:12]
        entry = self.pending.get(key)
        detail = redact(record.getMessage())[:500]
        if record.exc_info:
            detail += f' ({record.exc_info[0].__name__})'
        now = time.monotonic()
        repeated = self.repeats.setdefault(key, deque(maxlen=3))
        repeated.append(now)
        self.repeats.move_to_end(key)
        while len(self.repeats) > 512:
            self.repeats.popitem(last=False)
        emergency = record.levelno >= logging.CRITICAL or record.name == 'operations.delivery'
        if record.name in {'operations.process-loop', 'operations.publish-loop',
                           'operations.poll-loop', 'operations.stats-loop', 'operations.support'}:
            emergency = emergency or (len(repeated) == 3 and now-repeated[0] < 3600)
        title, explanation = explain_error(detail, record.name)
        event = {'title': title, 'explanation': explanation, 'detail': detail, 'channel': context,
                 'emergency': emergency, 'time': datetime.now(ZoneInfo('Europe/Moscow')).isoformat()}
        self.events.emit(logging.LogRecord('events', logging.INFO, '', 0,
                         redact(json.dumps(event, ensure_ascii=False)), (), None))
        if not emergency:
            return
        if entry:
            entry['count'] += 1
            entry['detail'] = detail
        elif len(self.pending) < 512:
            self.pending[key] = {'count': 1, 'detail': detail, 'where': record.name, 'channel': context,
                                 'time': datetime.fromtimestamp(record.created, ZoneInfo('Europe/Moscow')).strftime('%H:%M:%S')}
        else:
            self.overflow += 1

    async def flush_alerts(self, bot):
        if not self.admins:
            self.pending.clear()
            self.overflow = 0
            return
        now = time.monotonic()
        with self.lock:
            selected = [(key, value.copy()) for key, value in self.pending.items()
                        if now - self.last_sent.get(key, -10000) >= 1800][:4]
        if not selected and not self.overflow:
            return
        # Delivery events are verified immediately before notifying: a resolved post
        # must not keep generating alerts from an old in-memory error queue.
        from app import db
        verified = []
        for key, event in selected:
            if event['where'] == 'operations.delivery':
                match = re.search(r'пост #(\d+)', event['detail'], re.I)
                if match:
                    try:
                        post = await db.fetch_one('SELECT status, (SELECT error_type FROM delivery_attempts d WHERE d.post_id=posts.id ORDER BY started_at DESC LIMIT 1) AS error_type FROM posts WHERE id=?', (int(match[1]),))
                    except Exception:
                        # No unverified action advice; retry notification after DB recovery.
                        continue
                    if not post or post['status'] not in ('failed','uncertain','partial'):
                        with self.lock:
                            self.pending.pop(key, None)
                        continue
                    status = post['status']
                    cause = {'HTTPStatusError':'HTTP-сервис отклонил запрос при подготовке отправки.',
                             'TelegramForbiddenError':'Telegram отказал боту в доступе.',
                             'TelegramBadRequest':'Telegram отклонил содержимое или параметры отправки.',
                             'UnsafeURL':'Медиа не прошло проверку безопасности.',
                             'TelegramRetryAfter':'Telegram ограничил частоту отправки.'}.get(post.get('error_type'),'Причина сохранена в истории публикации.')
                    event['detail'] = (
                        f"Не отправлен пост #{match[1]}. {cause} Автоматические попытки завершены. Откройте историю канала: проверьте причину, медиа и права бота перед повтором."
                        if status == 'failed' else
                        f"Пост #{match[1]}: Telegram подтвердил только часть отправки. Автоматический повтор отключён. Сверьте сообщения в канале; не отправляйте весь пост повторно."
                        if status == 'partial' else
                        f"Пост #{match[1]}: результат отправки неизвестен. Автоматический повтор отключён. Сначала проверьте, появился ли пост в канале.")
            verified.append((key,event))
        selected = verified
        if not selected and not self.overflow:
            return
        lines = ['⚠️ <b>Нейропостинг: состояние работы</b>']
        for key, event in selected:
            title, explanation = explain_error(event['detail'], event['where'])
            context = event.get('channel')
            target = f"Канал: {context['title'][:80]} · владелец {context['owner']}" if context else 'Общая проверка сервиса — конкретный канал не определён'
            lines.append(f"<b>{html.escape(title)}</b>\n{html.escape(target)}\n{html.escape(explanation)}\nПервое событие: {event['time']} МСК. Событий: {event['count']}.")
        overflow = self.overflow
        if overflow:
            lines.append(f'Ещё событий: {overflow}. Подробности сохранены в журнале.')
        lines.append('Технические подробности сохранены в журнале сервера. Одинаковые события объединены.')
        self.sending = True
        try:
            results = []
            for admin_id in self.admins:
                try:
                    async with asyncio.timeout(10):
                        await bot.send_message(admin_id, '\n\n'.join(lines), parse_mode='HTML', disable_web_page_preview=True)
                    results.append(True)
                except Exception:
                    results.append(False)
                    logging.getLogger('operations.alert_transport').warning('Не удалось доставить алерт администратору', exc_info=True)
            if all(results):
                with self.lock:
                    for key, event in selected:
                        self.pending[key]['count'] -= event['count']
                        if not self.pending[key]['count']:
                            del self.pending[key]
                        self.last_sent[key] = now
                    self.overflow -= overflow
                    self.last_sent = {k: t for k, t in self.last_sent.items() if now-t < 1800}
        finally:
            self.sending = False

    async def run(self, bot):
        while True:
            await self.flush_alerts(bot)
            await asyncio.sleep(10)

    def close(self):
        self.disk.close()
        self.events.close()
        super().close()


def install(path):
    journal = ErrorJournal(path)
    logging.getLogger().addHandler(journal)
    # Redact console output as well, including formatted tracebacks.
    for handler in logging.getLogger().handlers:
        if handler is not journal:
            handler.setFormatter(SafeFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    return journal
