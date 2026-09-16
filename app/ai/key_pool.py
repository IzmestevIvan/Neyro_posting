"""Server-only Gemini credentials. Never return secret rows from HTTP endpoints."""
import re
from datetime import timedelta

from app import db

MAX_KEYS = 20
PUBLIC_COLUMNS = 'id,label,enabled,cooldown_until,last_error,last_used_at,last_success_at,created_at'


async def overview():
    return await db.fetch_all(f'SELECT {PUBLIC_COLUMNS} FROM service_api_keys ORDER BY id')


async def add_many(raw: str):
    keys = list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip()))
    # Auth keys are opaque and have a different length from legacy standard keys.
    # This only checks safe input syntax; Google verifies permissions and validity.
    if not keys or len(keys) > MAX_KEYS or any(not re.fullmatch(r'(?:AIza[A-Za-z0-9_-]{35}|AQ\.[A-Za-z0-9_.-]{20,509})', key) for key in keys):
        raise ValueError('Введите до 20 ключей Gemini из Google AI Studio, каждый с новой строки. Поддерживаются AIza… и AQ.…; внутри ключа не должно быть пробелов.')
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('LOCK TABLE service_api_keys IN SHARE ROW EXCLUSIVE MODE')
        existing = {
            row['secret'] for row in await conn.fetch('SELECT secret FROM service_api_keys')}
        new = [key for key in keys if key not in existing]
        if len(existing) + len(new) > MAX_KEYS:
            raise ValueError('В пуле может быть не больше 20 ключей. Удалите ненужные.')
        for key in new:
            row = await conn.fetchrow("INSERT INTO service_api_keys(label,secret) VALUES('Ключ Gemini',$1) RETURNING id", key)
            await conn.execute("UPDATE service_api_keys SET label=$1 WHERE id=$2", f"Ключ Gemini №{row['id']}", row['id'])
    return len(new)


async def candidates():
    return await db.fetch_all(
        'SELECT id,secret FROM service_api_keys WHERE enabled AND '
        '(cooldown_until IS NULL OR cooldown_until<=now()) '
        'ORDER BY last_used_at ASC NULLS FIRST,id LIMIT 3')


async def claim(key_id):
    return bool(await db.update(
        'UPDATE service_api_keys SET last_used_at=now() WHERE id=? AND enabled '
        'AND (cooldown_until IS NULL OR cooldown_until<=now())', (key_id,)))


async def succeeded(key_id):
    await db.execute('UPDATE service_api_keys SET last_success_at=now(),last_error=NULL,cooldown_until=NULL WHERE id=?', (key_id,))


async def failed(key_id, errors):
    if any(f'HTTP {status}' in errors for status in (401, 403)):
        reason, minutes = 'Нет доступа: проверьте ключ и разрешения проекта', 30
    elif 'HTTP 429' in errors:
        reason, minutes = 'Ограничение квоты или частоты запросов', 30
    else:
        reason, minutes = 'Провайдер временно недоступен или отклонил запрос', 2
    await db.execute('UPDATE service_api_keys SET last_error=?,cooldown_until=? WHERE id=?',
                     (reason, db.utcnow() + timedelta(minutes=minutes), key_id))
