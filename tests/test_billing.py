"""Payment integration tests: dedicated local DB and mocked YooKassa transport only."""
import asyncio
import copy
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api import account_auth, auth
from app.billing import routes, service
from app.billing.plans import PLANS, money, month_after, prorated_upgrade
from app.billing.provider import Settings, YooKassa, amount_kopecks, safe_confirmation
from app.core.publisher import used_today

ORIGIN = "https://billing.example.test"
USER = 71
OTHER = 72
BOT_TOKEN = "123456:TEST-NOT-A-REAL-BOT"


class MockYooKassa(YooKassa):
    def __init__(self, environment="live", enabled=True):
        settings = Settings("12345", environment + "_fixture_only_not_a_secret", environment,
                            enabled, ORIGIN, 1, "service", "full_payment", True)
        self.calls = []
        self.payments = {}
        self.by_key = {}
        self.timeout_once = False
        self.response_status = "pending"
        super().__init__(settings=settings, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        assert request.url.host == "api.yookassa.ru"
        body = json.loads(request.content) if request.content else None
        key = request.headers.get("Idempotence-Key")
        self.calls.append((request.method, request.url.path, key, copy.deepcopy(body)))
        if request.method == "POST" and request.url.path == "/v3/payments":
            assert key
            if key in self.by_key:
                payment = self.payments[self.by_key[key]]
            else:
                payment = {"id": str(uuid4()), "status": self.response_status,
                           "paid": self.response_status == "succeeded", "test": self.settings.environment == "test",
                           "amount": body["amount"], "metadata": body["metadata"],
                           "recipient": {"account_id": self.settings.shop_id},
                           "confirmation": {"confirmation_url": "https://yoomoney.ru/checkout/payments/fixture"},
                           "receipt_registration": "succeeded"}
                self.payments[payment["id"]] = payment
                self.by_key[key] = payment["id"]
            if self.timeout_once:
                self.timeout_once = False
                raise httpx.ReadTimeout("Simulated unknown payment outcome", request=request)
            return httpx.Response(200, json=payment)
        if request.method == "GET" and request.url.path.startswith("/v3/payments/"):
            return httpx.Response(200, json=self.payments[request.url.path.rsplit("/", 1)[-1]])
        raise AssertionError("Unexpected mocked provider request")

    def paid(self, order_id):
        payment = self.payments[self.by_key[str(order_id)]]
        payment.update(status="succeeded", paid=True)
        return copy.deepcopy(payment)


@pytest_asyncio.fixture
async def billing(store, monkeypatch):
    await store.execute("INSERT INTO users(tg_id,daily_limit,max_channels,created_at) VALUES(?,3,5,now()), (?,3,5,now())",
                        (USER, OTHER))
    provider = MockYooKassa()
    monkeypatch.setattr(service, "YooKassa", lambda: provider)
    monkeypatch.setattr(routes, "YooKassa", lambda: provider)
    monkeypatch.setattr(routes.Settings, "read", lambda: provider.settings)
    monkeypatch.setattr(account_auth, "PUBLIC_URL", ORIGIN)
    monkeypatch.setattr(auth, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(auth, "DEV_AUTH", False)
    return store, provider


async def purchase(billing, plan="plus", *, user_id=USER, paid=True, provider=None):
    store, default_provider = billing
    provider = provider or default_provider
    quote = await service.quote(user_id, plan)
    order = await service.checkout(user_id, quote["id"], uuid4(), "receipt@example.test", provider)
    if paid:
        await service.settle(order["order_id"], provider.paid(order["order_id"]), provider.settings)
        order = await service.get_order(user_id, order["order_id"])
    return quote, order


@pytest_asyncio.fixture
async def web(billing):
    store, _ = billing
    token = secrets.token_urlsafe(32)
    await store.execute("INSERT INTO web_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                        (account_auth._digest(token), USER, store.utcnow() + timedelta(hours=1)))
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        client.cookies.set(account_auth.SESSION_COOKIE, token)
        yield client, {"Origin": ORIGIN, "X-CSRF-Token": account_auth._derived(token, "account-csrf")}


def miniapp_header(user_id=USER):
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "Test"})}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Init-Data": urlencode(fields)}


def test_plan_catalog_and_calendar_proration():
    assert [(p.price, p.ai_daily_limit) for p in PLANS.values()] == [
        (39000, 5), (49000, 10), (59000, 15), (99000, 25), (149000, 50)]
    jan31 = datetime(2028, 1, 31, 12, tzinfo=timezone.utc)
    assert month_after(jan31) == jan31.replace(month=2, day=29)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=30)
    assert prorated_upgrade(PLANS["plus"], PLANS["expert"], start, end, start + timedelta(days=15)) == 10000
    assert prorated_upgrade(PLANS["plus"], PLANS["pro"], start, end, end - timedelta(seconds=1)) == 100
    assert money(39000) == "390.00"


async def test_live_settlement_is_idempotent_under_concurrency(billing):
    store, provider = billing
    quote, order = await purchase(billing, paid=False)
    assert order["status"] == "pending"
    assert not await store.fetch_one("SELECT * FROM billing_periods")
    payload = provider.paid(order["order_id"])
    await asyncio.gather(*[service.settle(order["order_id"], payload, provider.settings) for _ in range(3)])
    periods = await store.fetch_all("SELECT * FROM billing_periods")
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    assert len(periods) == 1
    assert user["daily_limit"] == 5 and user["plan"] == "plus"
    assert user["access_until"] == periods[0]["ends_at"]
    assert 27 <= (periods[0]["ends_at"] - periods[0]["starts_at"]).days <= 31
    events = await store.fetch_all("SELECT * FROM billing_events WHERE event='entitlement_applied'")
    assert len(events) == 1
    account = await store.fetch_one("SELECT * FROM billing_accounts WHERE user_id=?", (USER,))
    assert account["version"] == 1


async def test_test_payment_never_grants_real_access(billing):
    store, _ = billing
    provider = MockYooKassa("test")
    _, order = await purchase(billing, provider=provider)
    assert order["status"] == "test_succeeded" and order["test"] is True
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    assert user["access_until"] is None and user["daily_limit"] == 3
    assert not await store.fetch_one("SELECT * FROM billing_periods")


@pytest.mark.parametrize("field,value", [
    ("amount.value", "1.00"), ("amount.currency", "USD"), ("amount.value", "NaN"),
    ("metadata.user_id", "999"), ("metadata.order_id", str(uuid4())),
    ("recipient.account_id", "another-shop"), ("test", True), ("test", None),
    ("id", str(uuid4())), ("confirmation.confirmation_url", "https://attacker.example/pay"),
])
async def test_mismatched_provider_payment_is_held_without_access(billing, field, value):
    store, provider = billing
    _, order = await purchase(billing, paid=False)
    payload = provider.paid(order["order_id"])
    keys = field.split(".")
    target = payload
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    await service.settle(order["order_id"], payload, provider.settings)
    assert (await service.get_order(USER, order["order_id"]))["status"] == "review"
    assert not await store.fetch_one("SELECT * FROM billing_periods")
    assert (await store.fetch_one("SELECT access_until FROM users WHERE tg_id=?", (USER,)))["access_until"] is None


async def test_unknown_creation_outcome_retries_same_body_and_key(billing):
    store, provider = billing
    provider.timeout_once = True
    quote = await service.quote(USER, "pro")
    request_id = uuid4()
    first = await service.checkout(USER, quote["id"], request_id, "original@example.test", provider)
    assert first["status"] == "creating"
    other_quote = await service.quote(USER, "plus")
    with pytest.raises(service.BillingError, match="Предыдущий платёж"):
        await service.checkout(USER, other_quote["id"], uuid4(), "other@example.test", provider)
    second = await service.checkout(USER, quote["id"], request_id, "changed@example.test", provider)
    assert first["order_id"] == second["order_id"] and second["status"] == "pending"
    posts = [call for call in provider.calls if call[0] == "POST"]
    assert len(posts) == 2 and posts[0] == posts[1]
    assert posts[0][2] == first["order_id"]
    assert posts[0][3]["receipt"]["customer"]["email"] == "original@example.test"
    assert posts[0][3]["receipt"]["items"][0]["amount"] == {"value": "490.00", "currency": "RUB"}
    assert len(provider.payments) == 1
    assert len(await store.fetch_all("SELECT * FROM billing_orders")) == 1


async def test_duplicate_checkout_requests_create_one_provider_payment(billing):
    store, provider = billing
    quote = await service.quote(USER, "plus")
    request_id = uuid4()
    results = await asyncio.gather(*[service.checkout(USER, quote["id"], request_id, "a@example.test", provider) for _ in range(2)])
    assert results[0]["order_id"] == results[1]["order_id"]
    assert len(provider.payments) == 1
    assert len(await store.fetch_all("SELECT * FROM billing_orders")) == 1


async def test_different_concurrent_checkouts_leave_only_one_unresolved_order(billing):
    store, provider = billing
    quotes = [await service.quote(USER, plan) for plan in ("plus", "pro")]
    results = await asyncio.gather(*[
        service.checkout(USER, quote["id"], uuid4(), "a@example.test", provider)
        for quote in quotes], return_exceptions=True)
    assert sum(isinstance(result, service.BillingError) for result in results) == 1
    assert len(provider.payments) == 1
    assert len(await store.fetch_all("SELECT * FROM billing_orders")) == 1


async def test_eight_concurrent_provider_leases_do_not_exhaust_pool(billing, monkeypatch):
    store, provider = billing
    orders = []
    for user_id in range(200, 208):
        await store.execute("INSERT INTO users(tg_id,created_at) VALUES(?,now())", (user_id,))
        _, order = await purchase(billing, user_id=user_id, paid=False)
        provider.paid(order["order_id"])
        orders.append(order)
    original = provider.request
    reached = 0
    all_leases = asyncio.Event()

    async def gated_request(method, path, **kwargs):
        nonlocal reached
        reached += 1
        if reached == 8:
            all_leases.set()
        await all_leases.wait()
        return await original(method, path, **kwargs)

    monkeypatch.setattr(provider, "request", gated_request)
    async with asyncio.timeout(10):
        await asyncio.gather(*[service.refresh_order(order["order_id"], provider) for order in orders])
    assert len(await store.fetch_all("SELECT * FROM billing_periods")) == 8


@pytest.mark.parametrize("change", ["expiry", "version"])
async def test_stale_quote_never_calls_provider(billing, change):
    store, provider = billing
    quote = await service.quote(USER, "plus")
    if change == "expiry":
        await store.execute("UPDATE billing_quotes SET expires_at=now()-interval '1 second'")
    else:
        await store.execute("UPDATE billing_accounts SET version=version+1 WHERE user_id=?", (USER,))
    with pytest.raises(service.BillingError, match="устарел"):
        await service.checkout(USER, quote["id"], uuid4(), "a@example.test", provider)
    assert provider.calls == []
    assert not await store.fetch_one("SELECT * FROM billing_orders")


async def test_foreign_quote_and_reused_request_id_are_rejected(billing):
    _, provider = billing
    quote = await service.quote(USER, "plus")
    with pytest.raises(service.BillingError, match="не найден"):
        await service.checkout(OTHER, quote["id"], uuid4(), "a@example.test", provider)
    request_id = uuid4()
    await service.checkout(USER, quote["id"], request_id, "a@example.test", provider)
    other_quote = await service.quote(USER, "pro")
    with pytest.raises(service.BillingError, match="другому расчёту"):
        await service.checkout(USER, other_quote["id"], request_id, "a@example.test", provider)


async def test_upgrade_is_prorated_and_preserves_day_usage_and_end(billing):
    store, provider = billing
    await purchase(billing)
    now = store.utcnow()
    await store.execute("UPDATE billing_periods SET starts_at=?,ends_at=?", (now - timedelta(days=15), now + timedelta(days=15)))
    await store.execute("INSERT INTO delivery_attempts(owner_id,worker_id,status) VALUES(?, 'test', 'published'), (?, 'test', 'sending')", (USER, USER))
    old_period = await store.fetch_one("SELECT * FROM billing_periods")
    quote, order = await purchase(billing, "expert")
    assert quote["kind"] == "upgrade"
    assert 9900 <= quote["amount"] <= 10000
    assert quote["amount"] < PLANS["expert"].price
    assert order["status"] == "succeeded"
    period = await store.fetch_one("SELECT * FROM billing_periods")
    assert period["id"] == old_period["id"] and period["ends_at"] == old_period["ends_at"]
    assert period["plan"] == "expert"
    assert await used_today(USER) == 2
    subscription = await service.subscription(USER)
    assert subscription["ai_daily_limit"] == 15 and subscription["ai_used_today"] == 2
    assert subscription["period_end"] == old_period["ends_at"]


async def test_prepaid_renewal_activates_at_boundary(billing, monkeypatch):
    store, _ = billing
    await purchase(billing)
    first = await store.fetch_one("SELECT * FROM billing_periods")
    quote, _ = await purchase(billing)
    assert quote["kind"] == "renewal" and quote["amount"] == 39000
    periods = await store.fetch_all("SELECT * FROM billing_periods ORDER BY starts_at")
    assert len(periods) == 2 and periods[1]["starts_at"] == first["ends_at"]
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    assert user["access_until"] == first["ends_at"]
    monkeypatch.setattr(store, "utcnow", lambda: periods[1]["starts_at"] + timedelta(seconds=1))
    await service.sync_user(USER)
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    assert user["access_until"] == periods[1]["ends_at"]


async def test_upgrade_preserves_already_paid_next_period(billing):
    store, _ = billing
    await purchase(billing)
    await purchase(billing)
    before = await store.fetch_all("SELECT * FROM billing_periods ORDER BY starts_at")
    quote, _ = await purchase(billing, "expert")
    assert quote["kind"] == "upgrade"
    after = await store.fetch_all("SELECT * FROM billing_periods ORDER BY starts_at")
    assert after[0]["plan"] == "expert"
    assert after[0]["ends_at"] == before[0]["ends_at"]
    assert after[1] == before[1]


async def test_scheduled_downgrade_never_charges_or_changes_current_period(billing):
    store, provider = billing
    await purchase(billing, "pro")
    before = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    count = len(provider.calls)
    quote = await service.quote(USER, "plus")
    assert quote["kind"] == "downgrade" and quote["amount"] == 0
    result = await service.checkout(USER, quote["id"], uuid4(), None, provider)
    assert result["status"] == "scheduled" and result["confirmation_url"] is None
    assert len(provider.calls) == count
    after = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    assert after["daily_limit"] == 10 and after["access_until"] == before["access_until"]
    subscription = await service.subscription(USER)
    assert subscription["scheduled_plan"] == "plus" and subscription["auto_renew"] is False
    assert subscription["scheduled_at"] == before["access_until"]


async def test_expired_paid_access_restores_legacy_entitlement(billing, monkeypatch):
    store, _ = billing
    legacy = store.utcnow() + timedelta(days=90)
    await store.execute("UPDATE users SET access_until=?,daily_limit=7,plan='custom' WHERE tg_id=?", (legacy, USER))
    await purchase(billing)
    paid = await store.fetch_one("SELECT * FROM billing_periods")
    monkeypatch.setattr(store, "utcnow", lambda: paid["ends_at"] + timedelta(seconds=1))
    await service.sync_user(USER)
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    assert user["access_until"] == legacy and user["daily_limit"] == 7 and user["plan"] == "custom"


def refund_for(provider, order, amount=None):
    payment = provider.payments[provider.by_key[order["order_id"]]]
    return {"id": str(uuid4()), "payment_id": payment["id"], "status": "succeeded",
            "amount": {"value": amount or payment["amount"]["value"], "currency": "RUB"}}


async def test_full_refund_is_idempotent_revokes_paid_period_and_restores_legacy(billing):
    store, provider = billing
    legacy = store.utcnow() + timedelta(days=90)
    await store.execute("UPDATE users SET access_until=?,daily_limit=7,plan='custom' WHERE tg_id=?", (legacy, USER))
    _, order = await purchase(billing)
    refund = refund_for(provider, order)
    await asyncio.gather(*[service.record_refund(refund, provider) for _ in range(2)])
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id=?", (USER,))
    assert user["access_until"] == legacy and user["daily_limit"] == 7 and user["plan"] == "custom"
    assert (await store.fetch_one("SELECT * FROM billing_periods"))["revoked"] is True
    assert (await service.get_order(USER, order["order_id"]))["status"] == "refunded"
    assert len(await store.fetch_all("SELECT * FROM billing_refunds")) == 1
    assert len(await store.fetch_all("SELECT * FROM billing_events WHERE event='refund_verified'")) == 1


@pytest.mark.parametrize("upgrade", [False, True])
async def test_partial_refund_or_refunded_upgrade_requires_manual_review(billing, upgrade):
    store, provider = billing
    _, order = await purchase(billing)
    if upgrade:
        _, order = await purchase(billing, "pro")
    before = await store.fetch_one("SELECT * FROM billing_periods")
    await service.record_refund(refund_for(provider, order, None if upgrade else "1.00"), provider)
    assert (await store.fetch_one("SELECT * FROM billing_accounts WHERE user_id=?", (USER,)))["needs_review"] is True
    assert await store.fetch_one("SELECT * FROM billing_periods") == before
    with pytest.raises(service.BillingError, match="проверки"):
        await service.quote(USER, "expert")


async def test_full_base_refund_with_paid_upgrade_preserves_period_for_review(billing):
    store, provider = billing
    _, base_order = await purchase(billing)
    await purchase(billing, "pro")
    before = await store.fetch_one("SELECT * FROM billing_periods")
    await service.record_refund(refund_for(provider, base_order), provider)
    after = await store.fetch_one("SELECT * FROM billing_periods")
    assert after == before
    assert (await store.fetch_one("SELECT * FROM billing_accounts WHERE user_id=?", (USER,)))["needs_review"] is True


async def test_refund_arriving_before_payment_confirmation_does_not_grant_access(billing):
    store, provider = billing
    _, order = await purchase(billing, paid=False)
    payment = provider.paid(order["order_id"])
    await service.record_refund(refund_for(provider, order), provider)
    await service.settle(order["order_id"], payment, provider.settings)
    assert (await service.get_order(USER, order["order_id"]))["status"] == "refunded"
    assert not await store.fetch_one("SELECT * FROM billing_periods")


async def test_reconciliation_rotates_pending_orders_beyond_first_batch(billing):
    store, provider = billing
    orders = []
    for user_id in range(300, 332):
        await store.execute("INSERT INTO users(tg_id,created_at) VALUES(?,now())", (user_id,))
        _, order = await purchase(billing, user_id=user_id, paid=False)
        orders.append(order)
    provider.calls.clear()
    await service.reconcile_once(provider)
    await service.reconcile_once(provider)
    refreshed = {call[1].rsplit("/", 1)[-1] for call in provider.calls if call[0] == "GET"}
    assert refreshed == set(provider.by_key.values())


async def test_browser_checkout_requires_cookie_csrf_and_server_owned_price(web, billing):
    client, headers = web
    assert (await client.post("/api/billing/quote", json={"plan": "plus"})).status_code == 403
    assert (await client.post("/api/billing/quote", headers={"Origin": ORIGIN}, json={"plan": "plus"})).status_code == 403
    response = await client.post("/api/billing/quote", headers=headers, json={"plan": "plus", "amount": 1})
    assert response.status_code == 422
    response = await client.post("/api/billing/quote", headers=headers, json={"plan": "plus"})
    assert response.status_code == 200 and response.json()["amount_rub"] == "390.00"
    response = await client.post("/api/billing/checkout", headers=headers,
        json={"quote_id": response.json()["quote_id"], "request_id": str(uuid4()), "receipt_email": "a@example.test"})
    assert response.status_code == 200 and response.json()["status"] == "pending"


async def test_miniapp_can_read_access_but_cannot_quote_checkout_or_view_orders(web):
    client, _ = web
    client.cookies.clear()
    headers = miniapp_header()
    assert (await client.get("/api/billing/subscription", headers=headers)).status_code == 200
    assert (await client.post("/api/billing/quote", headers=headers, json={"plan": "plus"})).status_code == 401
    assert (await client.post("/api/billing/checkout", headers=headers,
        json={"quote_id": str(uuid4()), "request_id": str(uuid4()), "receipt_email": "a@example.test"})).status_code == 401
    assert (await client.get("/api/billing/orders", headers=headers)).status_code == 401


async def test_order_ownership_checked_before_provider_refresh(web, billing):
    client, _ = web
    _, provider = billing
    _, order = await purchase(billing, user_id=OTHER, paid=False)
    provider.calls.clear()
    assert (await client.get("/api/billing/orders/" + order["order_id"])).status_code == 404
    assert (await client.get("/api/billing/orders")).json() == {"orders": []}
    assert provider.calls == []


async def test_forged_webhook_fetches_provider_before_granting(web, billing):
    client, _ = web
    store, provider = billing
    _, order = await purchase(billing, paid=False)
    remote_id = provider.by_key[order["order_id"]]
    fake = {"type": "notification", "event": "payment.succeeded", "object": {
        "id": remote_id, "status": "succeeded", "paid": True, "amount": {"value": "999999.00", "currency": "RUB"},
        "metadata": {"order_id": order["order_id"], "user_id": str(USER)}}}
    client.cookies.clear()
    assert (await client.post("/api/billing/webhook", json=fake)).status_code == 200
    assert provider.calls[-1][:2] == ("GET", "/v3/payments/" + remote_id)
    assert (await service.get_order(USER, order["order_id"]))["status"] == "pending"
    assert not await store.fetch_one("SELECT * FROM billing_periods")
    provider.paid(order["order_id"])
    assert (await client.post("/api/billing/webhook", json=fake)).status_code == 200
    assert (await service.get_order(USER, order["order_id"]))["status"] == "succeeded"


async def test_unconfigured_provider_disables_checkout_without_http(billing):
    store, _ = billing
    provider = MockYooKassa(enabled=False)
    quote = await service.quote(USER, "plus")
    with pytest.raises(service.BillingError, match="не подключена"):
        await service.checkout(USER, quote["id"], uuid4(), "a@example.test", provider)
    assert provider.calls == []
    assert not await store.fetch_one("SELECT * FROM billing_orders")


@pytest.mark.parametrize("value", ["http://yoomoney.ru/pay", "https://user:pass@yoomoney.ru/pay",
                                  "https://yoomoney.ru.evil.test/pay", "https://yoomoney.ru:444/pay"])
def test_external_payment_redirect_is_restricted(value):
    with pytest.raises(ValueError):
        safe_confirmation(value)


@pytest.mark.parametrize("amount", [{"value": "1.001", "currency": "RUB"}, {"value": "-1", "currency": "RUB"},
                                   {"value": "Infinity", "currency": "RUB"}, {"value": "390.00", "currency": "USD"}])
def test_provider_amount_is_exact_nonnegative_rubles(amount):
    with pytest.raises(ValueError):
        amount_kopecks(amount)
