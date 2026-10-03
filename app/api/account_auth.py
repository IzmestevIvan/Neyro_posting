"""Telegram OIDC login for the standalone website, separate from Mini App auth."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.api.ingress import client_key
from app.config import ADMIN_IDS, PUBLIC_URL
from app.core.rate_limit import admit

TELEGRAM_LOGIN_CLIENT_ID = os.getenv("TELEGRAM_LOGIN_CLIENT_ID", "").strip()
ISSUER = "https://oauth.telegram.org"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
SESSION_COOKIE = "__Host-neyro_session"
CHALLENGE_COOKIE = "__Host-neyro_login"
SESSION_TTL = 24 * 3600
CHALLENGE_TTL = 10 * 60
JWKS_TTL = 3600
JWKS_REFRESH_INTERVAL = 60
MAX_JWKS_BYTES = 65536
MAX_JWKS_KEYS = 16
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{43}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expiry ON web_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(user_id);
CREATE TABLE IF NOT EXISTS web_login_challenges (
  token_hash TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_login_challenges_expiry ON web_login_challenges(expires_at);
"""

router = APIRouter(prefix="/api/account", tags=["account"])


def _unauthorized() -> HTTPException:
    return HTTPException(401, "Войдите через Telegram ещё раз.")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _derived(token: str, purpose: str) -> str:
    return hmac.new(token.encode("ascii"), purpose.encode("ascii"), hashlib.sha256).hexdigest()


def _cookie(request: Request, name: str) -> str:
    value = request.cookies.get(name, "")
    return value if _OPAQUE.fullmatch(value) else ""


def _client_id() -> str:
    value = TELEGRAM_LOGIN_CLIENT_ID
    return value if re.fullmatch(r"[1-9][0-9]{0,15}", value) and int(value) <= 2**53 - 1 else ""


def _origin() -> str:
    try:
        parsed = urlsplit(PUBLIC_URL)
        port = parsed.port
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("HTTPS website origin required")
        hostname = parsed.hostname
        if ":" in hostname:
            hostname = f"[{hostname}]"
        return f"https://{hostname}" + (f":{port}" if port and port != 443 else "")
    except ValueError as exc:
        raise HTTPException(503, "Вход на сайте пока не настроен.") from exc


def _same_origin(request: Request) -> None:
    if request.headers.getlist("origin") != [_origin()]:
        raise HTTPException(403, "Откройте личный кабинет на сайте сервиса.")


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _set_cookie(response: Response, name: str, token: str, lifetime: int) -> None:
    response.set_cookie(name, token, max_age=lifetime, path="/", secure=True, httponly=True, samesite="lax")


def _delete_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")


class TelegramKeys:
    """Fixed-host, bounded JWKS cache; unknown kids cannot trigger unlimited fetches."""

    def __init__(self):
        self.keys: dict[str, object] = {}
        self.expires = 0.0
        self.last_attempt = float("-inf")
        self.lock = asyncio.Lock()

    async def _fetch(self) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3),
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", JWKS_URL, headers={"Accept": "application/json"}) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_JWKS_BYTES:
                        raise ValueError("JWKS exceeds size limit")
        document = json.loads(content)
        items = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_JWKS_KEYS:
            raise ValueError("Invalid JWKS key count")
        keys = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid JWK")
            if item.get("kty") != "RSA" or item.get("alg", "RS256") != "RS256" or item.get("use", "sig") != "sig":
                continue
            kid = item.get("kid")
            if not isinstance(kid, str) or not 1 <= len(kid) <= 128 or kid in keys:
                raise ValueError("Invalid JWK identifier")
            key = jwt.PyJWK.from_dict(item, algorithm="RS256").key
            if key.key_size < 2048:
                raise ValueError("Invalid RSA key size")
            keys[kid] = key
        if not keys:
            raise ValueError("No signing keys")
        return keys

    async def get(self, kid: str):
        async with self.lock:
            now = time.monotonic()
            if kid in self.keys and now < self.expires:
                return self.keys[kid]
            if now - self.last_attempt >= JWKS_REFRESH_INTERVAL:
                self.last_attempt = now
                try:
                    # A total deadline also bounds a server streaming tiny chunks.
                    async with asyncio.timeout(6):
                        keys = await self._fetch()
                except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError, TimeoutError) as exc:
                    raise HTTPException(503, "Telegram временно недоступен. Повторите вход позже.") from exc
                self.keys = keys
                self.expires = time.monotonic() + JWKS_TTL
            if time.monotonic() >= self.expires:
                raise HTTPException(503, "Telegram временно недоступен. Повторите вход позже.")
            if kid not in self.keys:
                raise _unauthorized()
            return self.keys[kid]


_telegram_keys = TelegramKeys()


async def verify_id_token(id_token: str, nonce: str) -> dict:
    client_id = _client_id()
    if not client_id:
        raise HTTPException(503, "Вход через Telegram пока не настроен.")
    try:
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not 1 <= len(kid) <= 128:
            raise _unauthorized()
        key = await _telegram_keys.get(kid)
        claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=client_id,
                            issuer=ISSUER, leeway=30,
                            options={"require": ["iss", "aud", "exp", "iat", "sub", "id", "nonce"],
                                     "strict_aud": True})
        if (type(claims["iat"]) is not int or type(claims["exp"]) is not int
                or claims["exp"] <= time.time() or claims["iat"] > time.time() + 30
                or claims["iat"] < time.time() - CHALLENGE_TTL - 30
                or claims["exp"] <= claims["iat"]):
            raise _unauthorized()
        if not isinstance(claims["nonce"], str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise _unauthorized()
        # OIDC sub is opaque and is NOT Telegram's user ID; profile scope supplies id.
        if type(claims["id"]) is not int or not 0 < claims["id"] < 2**63:
            raise _unauthorized()
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
            raise _unauthorized()
        for field in ("name", "given_name", "preferred_username"):
            if claims.get(field) is not None and (not isinstance(claims[field], str) or len(claims[field]) > 256):
                raise _unauthorized()
        return {"id": claims["id"], "first_name": claims.get("given_name") or claims.get("name"),
                "username": claims.get("preferred_username")}
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise _unauthorized() from exc


async def _session_user(request: Request) -> dict | None:
    token = _cookie(request, SESSION_COOKIE)
    if not token:
        return None
    user = await db.fetch_one(
        "SELECT u.* FROM web_sessions s JOIN users u ON u.tg_id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > now()", (_digest(token),))
    if user and user["blocked"]:
        raise HTTPException(403, "Доступ заблокирован.")
    return user


async def web_user(request: Request) -> dict:
    """Authenticate only website cookies; Mini App initData cannot authorize this API."""
    user = await _session_user(request)
    if not user:
        raise _unauthorized()
    return user


async def verify_csrf(request: Request) -> None:
    """Use as a dependency on every cookie-authenticated state-changing route."""
    _same_origin(request)
    await web_user(request)
    received = request.headers.get("x-csrf-token", "")
    expected = _derived(_cookie(request, SESSION_COOKIE), "account-csrf")
    if not re.fullmatch(r"[0-9a-f]{64}", received) or not hmac.compare_digest(received, expected):
        raise HTTPException(403, "Обновите страницу и повторите действие.")


def _payload(request: Request, user: dict | None, token: str = "", nonce: str | None = None) -> dict:
    from app.api.auth import is_admin
    return {
        "authenticated": user is not None,
        "user": {**{field: user.get(field) for field in ("tg_id", "username", "first_name")},
                 "is_admin":is_admin(user)} if user else None,
        "csrf_token": _derived(token, "account-csrf") if token and user else None,
        "bot_username": getattr(request.app.state, "bot_username", ""),
        "login_nonce": nonce,
        "telegram_client_id": _client_id() or None,
        "login_method": "oidc",
    }


@router.get("/session")
async def account_session(request: Request, response: Response) -> dict:
    _no_store(response)
    if request.headers.get("origin"):
        _same_origin(request)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Откройте личный кабинет на сайте сервиса.")
    user = await _session_user(request)
    if user:
        return _payload(request, user, _cookie(request, SESSION_COOKIE))
    if not _client_id():
        return _payload(request, None)
    _origin()  # Fail closed until the HTTPS website origin is configured.
    if not admit(("web-login-challenge", client_key(request.scope)), 60):
        raise HTTPException(429, "Слишком много попыток входа. Повторите через минуту.", headers={"Retry-After": "60"})
    token = _cookie(request, CHALLENGE_COOKIE)
    challenge = await db.fetch_one(
        "SELECT token_hash FROM web_login_challenges WHERE token_hash = ? AND expires_at > now()",
        (_digest(token),)) if token else None
    if not challenge:
        token = secrets.token_urlsafe(32)
        await db.execute("DELETE FROM web_login_challenges WHERE expires_at <= now()")
        await db.execute("DELETE FROM web_sessions WHERE expires_at <= now()")
        await db.execute("INSERT INTO web_login_challenges (token_hash, expires_at) VALUES (?, ?)",
                         (_digest(token), db.utcnow() + timedelta(seconds=CHALLENGE_TTL)))
        _set_cookie(response, CHALLENGE_COOKIE, token, CHALLENGE_TTL)
    if request.cookies.get(SESSION_COOKIE):
        _delete_cookie(response, SESSION_COOKIE)
    return _payload(request, None, nonce=_derived(token, "telegram-login-nonce"))


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id_token: str = Field(min_length=1, max_length=16384)
    login_nonce: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/login")
async def account_login(body: LoginBody, request: Request, response: Response) -> dict:
    _no_store(response)
    _same_origin(request)
    if not admit(("web-login", client_key(request.scope)), 20):
        raise HTTPException(429, "Слишком много попыток входа. Повторите через минуту.", headers={"Retry-After": "60"})
    challenge_token = _cookie(request, CHALLENGE_COOKIE)
    if not challenge_token or not hmac.compare_digest(body.login_nonce, _derived(challenge_token, "telegram-login-nonce")):
        raise _unauthorized()
    challenge_hash = _digest(challenge_token)
    if not await db.fetch_one("SELECT token_hash FROM web_login_challenges WHERE token_hash = ? AND expires_at > now()",
                              (challenge_hash,)):
        raise _unauthorized()
    profile = await verify_id_token(body.id_token, body.login_nonce)
    token = secrets.token_urlsafe(32)
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        # The atomic delete decides the winner when two requests race the same nonce.
        challenge = await conn.fetchrow(
            "DELETE FROM web_login_challenges WHERE token_hash = $1 AND expires_at > now() RETURNING token_hash",
            challenge_hash)
        if not challenge:
            raise _unauthorized()
        user = dict(await conn.fetchrow(
            "INSERT INTO users (tg_id, username, first_name, is_admin, created_at) VALUES ($1, $2, $3, $4, now()) "
            "ON CONFLICT (tg_id) DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name RETURNING *",
            profile["id"], profile["username"], profile["first_name"], int(profile["id"] in ADMIN_IDS)))
        if user["blocked"]:
            raise HTTPException(403, "Доступ заблокирован.")
        previous = _cookie(request, SESSION_COOKIE)
        if previous:
            await conn.execute("DELETE FROM web_sessions WHERE token_hash = $1", _digest(previous))
        await conn.execute("INSERT INTO web_sessions (token_hash, user_id, expires_at) VALUES ($1, $2, $3)",
                           _digest(token), user["tg_id"], db.utcnow() + timedelta(seconds=SESSION_TTL))
    _set_cookie(response, SESSION_COOKIE, token, SESSION_TTL)
    _delete_cookie(response, CHALLENGE_COOKIE)
    return _payload(request, user, token)


@router.post("/logout")
async def account_logout(request: Request, response: Response) -> dict:
    _no_store(response)
    await verify_csrf(request)
    await db.execute("DELETE FROM web_sessions WHERE token_hash = ?", (_digest(_cookie(request, SESSION_COOKIE)),))
    _delete_cookie(response, SESSION_COOKIE)
    _delete_cookie(response, CHALLENGE_COOKIE)
    return {"ok": True}
