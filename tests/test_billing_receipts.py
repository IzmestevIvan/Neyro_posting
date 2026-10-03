"""NPD receipt administration: local database only, no payment or tax API calls."""
import asyncio
import json
import secrets
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api import account_auth, auth
from app.billing import receipts

ORIGIN = "https://billing.example.test"
ADMIN = 91
CUSTOMER = 92
RECEIPT = "https://lknpd.nalog.ru/api/v1/receipt/123456789012/200abcDEF9/print"
SECOND_RECEIPT = "https://lknpd.nalog.ru/api/v1/receipt/123456789012/200second9/print"


@pytest_asyncio.fixture
async def receipt_web(store, monkeypatch):
    monkeypatch.setattr(account_auth, "PUBLIC_URL", ORIGIN)
    monkeypatch.setattr(auth, "ADMIN_IDS", set())
    await store.execute(
        "INSERT INTO users(tg_id,is_admin,created_at) VALUES(?,1,now()), (?,0,now())", (ADMIN, CUSTOMER)
    )
    token = secrets.token_urlsafe(32)
    await store.execute(
        "INSERT INTO web_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
        (account_auth._digest(token), ADMIN, store.utcnow() + timedelta(hours=1)),
    )
    app = FastAPI()
    app.include_router(receipts.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        client.cookies.set(account_auth.SESSION_COOKIE, token)
        headers = {"Origin": ORIGIN, "X-CSRF-Token": account_auth._derived(token, "account-csrf")}
        yield store, client, headers


async def create_order(store, *, environment="live", status="succeeded", applied=True,
                       receipt_mode="npd_manual", receipt_url=None, description="Нейропостинг PLUS",
                       receipt_status=None):
    now = store.utcnow()
    quote_id, order_id = uuid4(), uuid4()
    await store.execute(
        "INSERT INTO billing_quotes(id,user_id,plan,kind,amount,account_version,period_start,period_end,expires_at) "
        "VALUES(?,?,'plus','new',39000,0,?,?,?)",
        (quote_id, CUSTOMER, now, now + timedelta(days=30), now + timedelta(minutes=10)),
    )
    await store.execute(
        "INSERT INTO billing_orders(id,user_id,quote_id,request_id,plan,kind,amount,account_version,"
        "period_start,period_end,environment,status,request_body,applied_at,receipt_mode,receipt_email,npd_receipt_url,receipt_status) "
        "VALUES(?,?,?,?,'plus','new',39000,0,?,?,?,?,?,?,?,?,?,?)",
        (order_id, CUSTOMER, quote_id, uuid4(), now, now + timedelta(days=30), environment, status,
         json.dumps({"description": description, "metadata": {"secret": "must-not-be-returned"}}),
         now if applied else None, receipt_mode, "customer@example.test", receipt_url,
         receipt_status or (("npd_attached" if receipt_url else "npd_pending")
                            if environment == "live" and receipt_mode == "npd_manual" and status == "succeeded" and applied else None)),
    )
    return order_id


async def test_pending_receipts_only_paid_live_unattached_npd(receipt_web):
    store, client, _ = receipt_web
    expected = await create_order(store)
    await create_order(store, environment="test", status="test_succeeded")
    await create_order(store, receipt_mode="yookassa_54fz")
    await create_order(store, applied=False)
    await create_order(store, status="canceled")
    await create_order(store, receipt_url=RECEIPT)
    await create_order(store, status="pending", applied=False)
    response = await client.get("/api/billing/admin/receipts")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    rows = response.json()["orders"]
    assert len(rows) == 1 and rows[0]["order_id"] == str(expected)
    assert rows[0]["amount_rub"] == "390.00" and rows[0]["plan"] == "plus"
    assert rows[0]["receipt_email"] == "customer@example.test"
    assert rows[0]["description"] == "Нейропостинг PLUS"
    assert "must-not-be-returned" not in response.text


async def test_attachment_race_is_idempotent_and_audited_once(receipt_web):
    store, client, headers = receipt_web
    order_id = await create_order(store)
    results = await asyncio.gather(*[
        client.post(f"/api/billing/admin/receipts/{order_id}", json={"receipt_url": RECEIPT}, headers=headers)
        for _ in range(3)
    ])
    assert all(response.status_code == 200 for response in results)
    assert results[0].json() == results[1].json() == results[2].json()
    assert results[0].json()["npd_receipt_url"] == RECEIPT
    assert results[0].json()["npd_receipt_attached_at"]
    assert (await store.fetch_one("SELECT receipt_status FROM billing_orders WHERE id=?", (order_id,)))["receipt_status"] == "npd_attached"
    assert (await client.get("/api/billing/admin/receipts")).json() == {"orders": [], "refunds": []}
    audit = await store.fetch_all("SELECT event FROM billing_events WHERE order_id=?", (order_id,))
    assert audit == [{"event": f"npd_receipt_attached:admin={ADMIN}"}]
    response = await client.post(f"/api/billing/admin/receipts/{order_id}",
                                 json={"receipt_url": SECOND_RECEIPT}, headers=headers)
    assert response.status_code == 409
    assert (await store.fetch_one("SELECT npd_receipt_url FROM billing_orders WHERE id=?", (order_id,)))["npd_receipt_url"] == RECEIPT


@pytest.mark.parametrize("overrides", [
    {"environment": "test", "status": "test_succeeded"}, {"status": "pending", "applied": False},
    {"status": "canceled"}, {"applied": False}, {"receipt_mode": "yookassa_54fz"}, {"status": "refunded"},
])
async def test_cannot_attach_receipt_to_ineligible_order(receipt_web, overrides):
    store, client, headers = receipt_web
    order_id = await create_order(store, **overrides)
    response = await client.post(f"/api/billing/admin/receipts/{order_id}", json={"receipt_url": RECEIPT}, headers=headers)
    assert response.status_code == 409
    assert not await store.fetch_all("SELECT * FROM billing_events")


@pytest.mark.parametrize("url", [
    "https://attacker.example/receipt", "http://lknpd.nalog.ru/api/v1/receipt/123456789012/200abcDEF9/print",
    RECEIPT + "?redirect=https://attacker.example", RECEIPT + "#fragment", RECEIPT + "/extra", RECEIPT + "\n",
    RECEIPT.replace("lknpd.nalog.ru", "lknpd.nalog.ru.attacker.example"),
    RECEIPT.replace("lknpd.nalog.ru", "user:password@lknpd.nalog.ru"),
    RECEIPT.replace("lknpd.nalog.ru", "lknpd.nalog.ru:8443"),
    RECEIPT.replace("200abcDEF9", "../receipt"), RECEIPT.replace("200abcDEF9", "%2e%2e"),
    RECEIPT.replace("123456789012", "１２３４５６７８９０１２"), "javascript:alert(1)",
])
async def test_untrusted_receipt_urls_are_rejected(receipt_web, url):
    store, client, headers = receipt_web
    order_id = await create_order(store)
    response = await client.post(f"/api/billing/admin/receipts/{order_id}", json={"receipt_url": url}, headers=headers)
    assert response.status_code == 422
    assert not await store.fetch_all("SELECT * FROM billing_events")


async def test_receipts_require_admin_website_session(receipt_web):
    store, client, headers = receipt_web
    order_id = await create_order(store)
    await store.execute("UPDATE users SET is_admin=0 WHERE tg_id=?", (ADMIN,))
    assert (await client.get("/api/billing/admin/receipts")).status_code == 403
    assert (await client.post(f"/api/billing/admin/receipts/{order_id}", json={"receipt_url": RECEIPT}, headers=headers)).status_code == 403
    client.cookies.clear()
    assert (await client.get("/api/billing/admin/receipts", headers={"X-Init-Data": "not-a-website-session"})).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Origin": ORIGIN}, {"Origin": "https://attacker.example"}])
async def test_attachment_requires_origin_and_csrf(receipt_web, headers):
    store, client, valid_headers = receipt_web
    order_id = await create_order(store)
    if headers.get("Origin") == "https://attacker.example":
        headers = {**headers, "X-CSRF-Token": valid_headers["X-CSRF-Token"]}
    response = await client.post(f"/api/billing/admin/receipts/{order_id}", json={"receipt_url": RECEIPT}, headers=headers)
    assert response.status_code == 403
    assert not await store.fetch_all("SELECT * FROM billing_events")


async def test_unknown_order_and_unexpected_fields_are_rejected(receipt_web):
    _, client, headers = receipt_web
    response = await client.post(f"/api/billing/admin/receipts/{uuid4()}", json={"receipt_url": RECEIPT}, headers=headers)
    assert response.status_code == 404
    response = await client.post(f"/api/billing/admin/receipts/{uuid4()}",
                                 json={"receipt_url": RECEIPT, "user_id": CUSTOMER}, headers=headers)
    assert response.status_code == 422


async def test_verified_capture_in_review_can_receive_receipt_without_access_grant(receipt_web):
    store, client, headers = receipt_web
    order_id = await create_order(store, status="review", applied=False, receipt_status="npd_pending")
    response = await client.get("/api/billing/admin/receipts")
    assert [row["order_id"] for row in response.json()["orders"]] == [str(order_id)]
    response = await client.post(f"/api/billing/admin/receipts/{order_id}", json={"receipt_url": RECEIPT}, headers=headers)
    assert response.status_code == 200
    record = await store.fetch_one("SELECT * FROM billing_orders WHERE id=?", (order_id,))
    assert record["status"] == "review" and record["applied_at"] is None
    assert record["receipt_status"] == "npd_attached"
