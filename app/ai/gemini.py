import asyncio
import json
import logging
import re
import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app import db
from app.ai import key_pool
from app.ai.metrics import metrics
from app.core import runtime
from app.core.runtime import ai_slots
from app.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL_FALLBACK,
    GEMINI_MODEL_MAIN,
    GEMINI_MODEL_VERIFY,
)

log = logging.getLogger("ai")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
COOLDOWN_KEY = "main_model_cooldown_until"
COOLDOWN_MINUTES = 30
JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


class AIError(RuntimeError):
    pass


class NoKeyError(AIError):
    pass


async def cooldown_left() -> int:
    until = await db.get_kv(COOLDOWN_KEY)
    if not until:
        return 0
    delta = datetime.fromisoformat(until) - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds() // 60))


async def check_models() -> dict[str, str]:
    """Пингует настроенные модели одним коротким запросом каждую.

    Google снимает модели с публикации, и мёртвое имя выглядит как «нейросеть молчит»:
    посты копятся, а причина видна только в логе конкретного запроса. Проверка на старте
    называет проблему сразу.
    """
    if not GEMINI_API_KEY:
        return {}
    result = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for role, model in (
            ("основная", GEMINI_MODEL_MAIN),
            ("фактчек", GEMINI_MODEL_VERIFY),
            ("резервная", GEMINI_MODEL_FALLBACK),
        ):
            try:
                await _call(client, model, GEMINI_API_KEY, "ping", None, 0.0, False)
                result[role] = f"{model} — ок"
            except Exception as exc:
                result[role] = f"{model} — НЕ РАБОТАЕТ: {str(exc)[:130]}"
    return result


def _is_transient(message: str) -> bool:
    """Quota (429) and server errors mean 'try the backup model'; 400/403 mean the key or
    the request is wrong and switching models would fail the same way."""
    if "HTTP 429" in message or "RESOURCE_EXHAUSTED" in message:
        return True
    return not any(f"HTTP {code}" in message for code in (400, 401, 403, 404))


async def _start_cooldown() -> None:
    until = datetime.now(timezone.utc) + timedelta(minutes=COOLDOWN_MINUTES)
    await db.set_kv(COOLDOWN_KEY, until.isoformat())
    log.info("main model on cooldown until %s", until)


async def _call(
    client: httpx.AsyncClient,
    model: str,
    api_key: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    as_json: bool,
    search: bool = False,
) -> str:
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if search:
        body['tools'] = [{'google_search': {}}]
    if as_json and not search:
        body["generationConfig"]["responseMimeType"] = "application/json"

    metrics.waiting += 1
    acquired = False
    try:
        async with ai_slots:
            acquired = True
            metrics.waiting -= 1
            metrics.active += 1
            started = time.monotonic()
            status = 'interrupted'
            try:
                resp = await client.post(
                    API.format(model=model),
                    headers={"x-goog-api-key": api_key},
                    json=body,
                    timeout=60,
                )
                status = resp.status_code
            except (httpx.RequestError, TimeoutError):
                status = 'transport'
                raise
            finally:
                metrics.active -= 1
                metrics.record(status, time.monotonic() - started)
    finally:
        if not acquired:
            metrics.waiting -= 1
    if resp.status_code != 200:
        raise AIError(f"{model}: HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise AIError(f'{model}: некорректный ответ провайдера') from exc
    if not isinstance(data, dict):
        raise AIError(f'{model}: некорректная структура ответа')
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "empty response")
        raise AIError(f"{model}: {reason}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise AIError(f"{model}: {candidates[0].get('finishReason', 'no text')}")
    if search:
        metadata = candidates[0].get('groundingMetadata') or {}
        sources = [chunk['web'] for chunk in metadata.get('groundingChunks', []) if isinstance(chunk, dict) and isinstance(chunk.get('web'), dict)]
        if not sources or not metadata.get('groundingSupports'):
            raise AIError('Поиск не предоставил подтверждающих ссылок')
        return json.dumps({'text': text[:10000], 'sources': sources[:12],
                           'search_entry': (metadata.get('searchEntryPoint') or {}).get('renderedContent','')[:30000]}, ensure_ascii=False)
    return text


async def generate(
    prompt: str,
    *,
    api_key: Optional[str] = None,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.8,
    as_json: bool = False,
    allow_fallback: bool = True,
    search: bool = False,
) -> str:
    customer = (api_key or '').strip()
    shared = GEMINI_API_KEY.strip()
    routes = []
    if customer:
        routes.append({'secret': customer, 'id': None, 'label': 'client' if customer != shared else 'service'})
    for row in await key_pool.candidates():
        if row['secret'] != customer and row['secret'] != shared:
            routes.append(dict(row, label=f"service pool #{row['id']}"))
    if shared and shared != customer:
        routes.append({'secret': shared, 'id': None, 'label': 'service'})
    if not routes:
        raise NoKeyError('Нет доступных ключей Gemini. Проверьте пул ключей в админ-панели.')
    primary = model or GEMINI_MODEL_MAIN
    chain = [primary]
    if allow_fallback:
        chain = list(dict.fromkeys([primary, GEMINI_MODEL_FALLBACK, GEMINI_MODEL_VERIFY, GEMINI_MODEL_MAIN]))
    errors = []
    deadline = time.monotonic() + 180
    async with httpx.AsyncClient() as client:
        for route in routes:
            current_key, key_id = route['secret'], route['id']
            lock = runtime.channel_lock(('gemini-key', hashlib.sha256(current_key.encode()).hexdigest()))
            # Busy keys are skipped so one slow account cannot hold up all channels.
            if lock.locked():
                continue
            async with lock:
                if key_id is not None and not await key_pool.claim(key_id):
                    continue
                models = list(chain)
                if current_key == shared and await cooldown_left() and len(models) > 1:
                    models = [m for m in models if m != GEMINI_MODEL_MAIN] + [GEMINI_MODEL_MAIN]
                operational_failure = False
                route_errors = []
                for attempt, name in enumerate(models):
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise AIError('Превышено время ожидания ИИ; материал остаётся в очереди')
                    try:
                        async with asyncio.timeout(min(60, left)):
                            if search:
                                result = await _call(client, name, current_key, prompt, system, temperature, as_json, search=True)
                            else:
                                result = await _call(client, name, current_key, prompt, system, temperature, as_json)
                        if key_id is not None:
                            await key_pool.succeeded(key_id)
                        if errors:
                            metrics.fallback()
                            log.warning('Обработка продолжена: резервный маршрут ИИ успешно ответил. Модель: %s; маршрут: %s', name, route['label'])
                        return result
                    except (AIError, httpx.RequestError, TimeoutError) as exc:
                        detail = str(exc) or type(exc).__name__
                        errors.append(detail)
                        route_errors.append(detail)
                        log.warning('gemini call failed (%s): %s', route['label'], detail)
                        operational_failure = operational_failure or isinstance(exc, (httpx.RequestError, TimeoutError)) or bool(re.search(r'HTTP (?:400|401|403|404|429|5\d\d)\b', detail))
                        if current_key == shared and name == GEMINI_MODEL_MAIN and _is_transient(detail):
                            await _start_cooldown()
                        if re.search(r'HTTP (?:401|403)\b', detail) or ('HTTP 400' in detail and not search) or (route['label'] == 'client' and 'HTTP 429' in detail):
                            break
                        if attempt + 1 < len(models):
                            await asyncio.sleep(1.5)
                if not operational_failure:
                    break  # Content/safety refusal is not a reason to switch accounts.
                if key_id is not None and not (search and all('HTTP 400' in e for e in route_errors)):
                    await key_pool.failed(key_id, '; '.join(route_errors))
    raise AIError('; '.join(errors) or 'Все доступные ключи заняты. Материал ожидает повторной обработки.')


async def generate_json(prompt: str, **kwargs) -> dict:
    raw = await generate(prompt, as_json=True, **kwargs)
    block = JSON_BLOCK.search(raw)
    payload = block.group(1) if block else raw
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AIError("модель вернула не JSON") from exc
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def verify_model() -> str:
    return GEMINI_MODEL_VERIFY
