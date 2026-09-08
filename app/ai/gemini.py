import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app import db
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
    log.warning("main model on cooldown until %s", until)


async def _call(
    client: httpx.AsyncClient,
    model: str,
    api_key: str,
    prompt: str,
    system: Optional[str],
    temperature: float,
    as_json: bool,
) -> str:
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if as_json:
        body["generationConfig"]["responseMimeType"] = "application/json"

    resp = await client.post(
        API.format(model=model),
        headers={"x-goog-api-key": api_key},
        json=body,
        timeout=90,
    )
    if resp.status_code != 200:
        raise AIError(f"{model}: HTTP {resp.status_code} {resp.text[:200]}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "empty response")
        raise AIError(f"{model}: {reason}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise AIError(f"{model}: {candidates[0].get('finishReason', 'no text')}")
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
) -> str:
    key = (api_key or GEMINI_API_KEY or "").strip()
    if not key:
        raise NoKeyError("не задан ключ Gemini")

    primary = model or GEMINI_MODEL_MAIN
    chain = [primary]
    if allow_fallback and GEMINI_MODEL_FALLBACK != primary:
        chain.append(GEMINI_MODEL_FALLBACK)
    if await cooldown_left() and len(chain) > 1:
        chain.reverse()

    errors = []
    async with httpx.AsyncClient() as client:
        for attempt, name in enumerate(chain):
            try:
                return await _call(client, name, key, prompt, system, temperature, as_json)
            except (AIError, httpx.RequestError) as exc:
                errors.append(str(exc))
                log.warning("gemini call failed: %s", exc)
                if name == primary and _is_transient(str(exc)):
                    await _start_cooldown()
                if attempt + 1 < len(chain):
                    await asyncio.sleep(1.5)
    raise AIError("; ".join(errors))


async def generate_json(prompt: str, **kwargs) -> dict:
    raw = await generate(prompt, as_json=True, **kwargs)
    block = JSON_BLOCK.search(raw)
    payload = block.group(1) if block else raw
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AIError(f"модель вернула не JSON: {payload[:200]}") from exc
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def verify_model() -> str:
    return GEMINI_MODEL_VERIFY
