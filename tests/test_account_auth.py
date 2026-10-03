"""Website login tests use generated RSA keys, mocked Telegram HTTP, and neyro_test."""
import asyncio
import json
import time
from datetime import timedelta

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException

from app.api import account_auth as auth

HTTP_CLIENT = httpx.AsyncClient
ORIGIN = "https://account.example.test"
CLIENT_ID = "123456789"
NONCE = "a" * 64


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def telegram(monkeypatch, signing_key):
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public_jwk.update(kid="telegram-key", alg="RS256", use="sig")
    requests = []
    result = {"keys": [public_jwk]}

    def respond(request):
        assert str(request.url) == auth.JWKS_URL
        assert request.method == "GET"
        requests.append(request)
        return httpx.Response(200, json=result)

    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(auth, "_telegram_keys", auth.TelegramKeys())
    monkeypatch.setattr(auth, "PUBLIC_URL", ORIGIN)
    monkeypatch.setattr(auth, "TELEGRAM_LOGIN_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(auth, "ADMIN_IDS", set())

    def sign(nonce=NONCE, **overrides):
        now = int(time.time())
        claims = {"iss": auth.ISSUER, "aud": CLIENT_ID, "sub": "opaque-pairwise-subject", "id": 42,
                  "name": "Иван Тест", "given_name": "Иван", "preferred_username": "test_user",
                  "iat": now, "exp": now + 3600, "nonce": nonce}
        claims.update(overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "telegram-key"})

    return sign, requests, result


@pytest_asyncio.fixture
async def browser(store, telegram):
    # Explicit setup makes this suite independent of where db.connect loads schemas.
    pool = await store.connect()
    await pool.execute(auth.SCHEMA)
    await pool.execute("TRUNCATE web_login_challenges, web_sessions")
    app = FastAPI()
    app.state.bot_username = "neyro_test_bot"
    app.include_router(auth.router)

    @app.get("/private")
    async def private(user=Depends(auth.web_user)):
        return {"user_id": user["tg_id"]}

    @app.post("/private", dependencies=[Depends(auth.verify_csrf)])
    async def mutation(user=Depends(auth.web_user)):
        return {"user_id": user["tg_id"]}

    async with HTTP_CLIENT(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        yield client


async def login(browser, telegram, **claims):
    session = (await browser.get("/api/account/session")).json()
    token = telegram[0](nonce=session["login_nonce"], **claims)
    result = await browser.post("/api/account/login", headers={"Origin": ORIGIN},
                                json={"id_token": token, "login_nonce": session["login_nonce"]})
    return result


async def test_signed_oidc_profile_uses_telegram_id_not_subject(telegram):
    user = await auth.verify_id_token(telegram[0](), NONCE)
    assert user == {"id": 42, "first_name": "Иван", "username": "test_user"}
    assert len(telegram[1]) == 1
    await auth.verify_id_token(telegram[0](), NONCE)
    assert len(telegram[1]) == 1


@pytest.mark.parametrize("overrides", [
    {"iss": "https://attacker.example"}, {"aud": "other-client"}, {"aud": [CLIENT_ID]},
    {"exp": 1}, {"exp": None}, {"iat": None}, {"iat": 1},
    {"iat": int(time.time()) + 10000}, {"iat": time.time()},
    {"nonce": "other-nonce"}, {"nonce": None}, {"nonce": "я" * 64},
    {"id": None}, {"id": "42"}, {"id": True}, {"id": 0}, {"id": 2**63},
    {"sub": 42}, {"sub": ""}, {"given_name": 42}, {"name": "x" * 257},
])
async def test_invalid_oidc_claims_are_rejected(telegram, overrides):
    with pytest.raises(HTTPException) as error:
        await auth.verify_id_token(telegram[0](**overrides), NONCE)
    assert error.value.status_code == 401


async def test_invalid_signatures_and_algorithms_are_rejected(telegram):
    token = telegram[0]()
    pieces = token.split(".")
    pieces[2] = ("A" if pieces[2][0] != "A" else "B") + pieces[2][1:]
    for bad in ("malformed-token", ".".join(pieces),
                jwt.encode({"id": 42}, "attacker-key-that-is-at-least-32-bytes", algorithm="HS256", headers={"kid": "telegram-key"})):
        with pytest.raises(HTTPException) as error:
            await auth.verify_id_token(bad, NONCE)
        assert error.value.status_code == 401


async def test_unknown_kid_fetches_are_throttled_and_key_rotation_works(telegram):
    await auth.verify_id_token(telegram[0](), NONCE)
    for _ in range(5):
        with pytest.raises(HTTPException) as error:
            await auth._telegram_keys.get("new-key")
        assert error.value.status_code == 401
    assert len(telegram[1]) == 1
    telegram[2]["keys"][0]["kid"] = "new-key"
    auth._telegram_keys.last_attempt -= auth.JWKS_REFRESH_INTERVAL
    assert await auth._telegram_keys.get("new-key")
    assert len(telegram[1]) == 2
    assert len(auth._telegram_keys.keys) == 1


@pytest.mark.parametrize("mode", ["oversize", "too_many", "broken_json", "redirect", "timeout"])
async def test_jwks_failures_fail_closed_and_do_not_retry_unbounded(telegram, monkeypatch, mode):
    requests = []

    def fail(request):
        requests.append(request)
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * (auth.MAX_JWKS_BYTES + 1))
        if mode == "too_many":
            return httpx.Response(200, json={"keys": telegram[2]["keys"] * (auth.MAX_JWKS_KEYS + 1)})
        if mode == "broken_json":
            return httpx.Response(200, content=b"not-json")
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.example/keys"})
        raise httpx.ReadTimeout("test timeout", request=request)

    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda **kwargs: HTTP_CLIENT(transport=httpx.MockTransport(fail), **kwargs))
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            await auth.verify_id_token(telegram[0](), NONCE)
        assert error.value.status_code == 503
    assert len(requests) == 1


async def test_login_cookie_session_and_logout(browser, telegram, store):
    response = await login(browser, telegram)
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is True
    assert data["user"]["tg_id"] == 42
    assert data["login_method"] == "oidc"
    assert data["login_nonce"] is None
    assert data["telegram_client_id"] == CLIENT_ID
    assert data["bot_username"] == "neyro_test_bot"
    cookie = next(value for value in response.headers.get_list("set-cookie") if value.startswith(auth.SESSION_COOKIE + "="))
    assert all(flag in cookie for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=86400"))
    assert "Domain=" not in cookie
    assert response.headers["cache-control"] == "no-store"
    session_token = browser.cookies.get(auth.SESSION_COOKIE)
    stored = await store.fetch_one("SELECT * FROM web_sessions")
    assert stored["token_hash"] == auth._digest(session_token)
    assert session_token not in str(stored)
    assert 86390 < (stored["expires_at"] - stored["created_at"]).total_seconds() <= 86401
    assert (await browser.get("/private")).json() == {"user_id": 42}
    refreshed = (await browser.get("/api/account/session")).json()
    assert refreshed["csrf_token"] == data["csrf_token"]
    headers = {"Origin": ORIGIN, "X-CSRF-Token": data["csrf_token"]}
    assert (await browser.post("/private", headers=headers)).status_code == 200
    assert (await browser.post("/api/account/logout", headers=headers)).status_code == 200
    assert not await store.fetch_one("SELECT * FROM web_sessions")
    assert (await browser.get("/private")).status_code == 401


async def test_login_challenge_is_browser_bound_and_one_use(browser, telegram, store):
    response = await browser.get("/api/account/session")
    nonce = response.json()["login_nonce"]
    challenge_token = browser.cookies.get(auth.CHALLENGE_COOKIE)
    assert (await browser.get("/api/account/session")).json()["login_nonce"] == nonce
    stored = await store.fetch_one("SELECT * FROM web_login_challenges")
    assert stored["token_hash"] == auth._digest(challenge_token)
    assert 590 < (stored["expires_at"] - stored["created_at"]).total_seconds() <= 601
    body = {"id_token": telegram[0](nonce=nonce), "login_nonce": nonce}
    browser.cookies.clear()
    assert (await browser.post("/api/account/login", json=body, headers={"Origin": ORIGIN})).status_code == 401
    browser.cookies.set(auth.CHALLENGE_COOKIE, challenge_token)
    assert (await browser.post("/api/account/login", json=body, headers={"Origin": ORIGIN})).status_code == 200
    browser.cookies.clear()
    browser.cookies.set(auth.CHALLENGE_COOKIE, challenge_token)
    assert (await browser.post("/api/account/login", json=body, headers={"Origin": ORIGIN})).status_code == 401


async def test_concurrent_login_accepts_challenge_only_once(browser, telegram, store):
    nonce = (await browser.get("/api/account/session")).json()["login_nonce"]
    body = {"id_token": telegram[0](nonce=nonce), "login_nonce": nonce}
    responses = await asyncio.gather(*[
        browser.post("/api/account/login", json=body, headers={"Origin": ORIGIN}) for _ in range(2)])
    assert sorted(response.status_code for response in responses) == [200, 401]
    assert len(await store.fetch_all("SELECT * FROM web_sessions")) == 1


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example", "http://account.example.test", ORIGIN + "/"])
async def test_login_rejects_wrong_or_missing_origin(browser, telegram, origin):
    nonce = (await browser.get("/api/account/session")).json()["login_nonce"]
    headers = {"Origin": origin} if origin is not None else {}
    response = await browser.post("/api/account/login", headers=headers,
                                  json={"id_token": telegram[0](nonce=nonce), "login_nonce": nonce})
    assert response.status_code == 403
    assert telegram[1] == []


async def test_cross_site_session_cannot_issue_challenge(browser, store):
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert (await browser.get("/api/account/session", headers=headers)).status_code == 403
    assert not await store.fetch_one("SELECT * FROM web_login_challenges")


async def test_csrf_required_for_mutation_and_logout(browser, telegram):
    data = (await login(browser, telegram)).json()
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "bad"},
                    {"Origin": "https://evil.example", "X-CSRF-Token": data["csrf_token"]}):
        assert (await browser.post("/api/account/logout", headers=headers)).status_code == 403
        assert (await browser.post("/private", headers=headers)).status_code == 403
    assert (await browser.get("/private")).status_code == 200


async def test_expired_challenge_and_session_cannot_authenticate(browser, telegram, store):
    nonce = (await browser.get("/api/account/session")).json()["login_nonce"]
    await store.execute("UPDATE web_login_challenges SET expires_at = ?", (store.utcnow() - timedelta(seconds=1),))
    response = await browser.post("/api/account/login", headers={"Origin": ORIGIN},
                                  json={"id_token": telegram[0](nonce=nonce), "login_nonce": nonce})
    assert response.status_code == 401
    assert telegram[1] == []
    assert (await login(browser, telegram)).status_code == 200
    await store.execute("UPDATE web_sessions SET expires_at = ?", (store.utcnow() - timedelta(seconds=1),))
    assert (await browser.get("/private")).status_code == 401
    assert (await browser.get("/api/account/session")).json()["authenticated"] is False


async def test_blocked_user_cannot_login_or_keep_session(browser, telegram, store):
    assert (await login(browser, telegram)).status_code == 200
    await store.execute("UPDATE users SET blocked = 1 WHERE tg_id = 42")
    assert (await browser.get("/private")).status_code == 403
    browser.cookies.clear()
    assert (await login(browser, telegram)).status_code == 403


async def test_website_does_not_accept_miniapp_or_dev_headers(browser):
    assert (await browser.get("/private", headers={"X-Init-Data": "dev:1"})).status_code == 401


async def test_unconfigured_login_is_disabled(browser, monkeypatch):
    monkeypatch.setattr(auth, "TELEGRAM_LOGIN_CLIENT_ID", "")
    data = (await browser.get("/api/account/session")).json()
    assert data["telegram_client_id"] is None
    assert data["login_nonce"] is None
    assert auth.CHALLENGE_COOKIE not in browser.cookies
