"""Self-employed receipt mode with mock payments; no external or fiscal calls."""
import asyncio
import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from app.billing import receipts, service
from tests.test_billing import MockYooKassa, USER, billing, purchase, refund_for


def use_npd(provider):
    provider.settings = replace(provider.settings, receipt_mode="npd_manual", vat_code=None,
                                payment_subject="", payment_mode="")
    return provider


def test_npd_requires_explicit_fiscal_readiness_and_valid_mode():
    provider = use_npd(MockYooKassa())
    assert provider.settings.ready()
    assert not replace(provider.settings, fiscal_ready=False).ready()
    assert not replace(provider.settings, receipt_mode="unrecognized").ready()
    assert not replace(provider.settings, receipt_mode="yookassa_54fz").ready()
    assert not replace(provider.settings, enabled=False).ready()


async def test_npd_checkout_omits_54fz_receipt_and_persists_customer_email(billing):
    store, provider = billing
    use_npd(provider)
    _, order = await purchase(billing, paid=False)
    assert order["status"] == "pending" and order["receipt_mode"] == "npd_manual"
    creates = [call for call in provider.calls if call[0] == "POST"]
    assert len(creates) == 1
    body = creates[0][3]
    assert "receipt" not in body and "save_payment_method" not in body
    assert "receipt@example.test" not in json.dumps(body)
    record = await store.fetch_one("SELECT * FROM billing_orders WHERE id=?", (UUID(order["order_id"]),))
    assert record["receipt_mode"] == "npd_manual" and record["receipt_email"] == "receipt@example.test"
    assert record["npd_receipt_url"] is None
    assert order["npd_receipt_url"] is None


async def test_verified_npd_success_creates_one_receipt_task_with_duplicate_notifications(billing):
    store, provider = billing
    use_npd(provider)
    _, order = await purchase(billing, paid=False)
    payload = provider.paid(order["order_id"])
    await asyncio.gather(*[service.settle(order["order_id"], payload, provider.settings) for _ in range(3)])
    saved = await service.get_order(USER, order["order_id"])
    assert saved["status"] == "succeeded" and saved["receipt_status"] == "npd_pending"
    events = await store.fetch_all("SELECT * FROM billing_events WHERE event='npd_receipt_required'")
    assert len(events) == 1 and str(events[0]["order_id"]) == order["order_id"]
    pending = await receipts.pending_receipts(user={"tg_id": 999})
    assert len(pending["orders"]) == 1 and pending["orders"][0]["order_id"] == order["order_id"]


async def test_test_npd_payment_creates_no_real_receipt_task(billing):
    store, _ = billing
    provider = use_npd(MockYooKassa(environment="test"))
    _, order = await purchase(billing, provider=provider)
    assert order["status"] == "test_succeeded" and order["test"]
    assert not await store.fetch_all("SELECT * FROM billing_events WHERE event='npd_receipt_required'")
    assert (await receipts.pending_receipts(user={"tg_id": 999})) == {"orders": [], "refunds": []}
    assert not await store.fetch_all("SELECT * FROM billing_periods")


@pytest.mark.parametrize("receipt_status", ["npd_pending", "npd_attached"])
async def test_provider_audit_and_duplicate_webhook_preserve_npd_receipt_state(billing, receipt_status):
    store, provider = billing
    use_npd(provider)
    _, order = await purchase(billing)
    await store.execute("UPDATE billing_orders SET receipt_status=? WHERE id=?", (receipt_status, UUID(order["order_id"])))
    payload = provider.paid(order["order_id"])
    provider.payments[payload["id"]]["receipt_registration"] = "canceled"
    await service.audit_order(order["order_id"], provider)
    await service.settle(order["order_id"], payload, provider.settings)
    assert (await service.get_order(USER, order["order_id"]))["receipt_status"] == receipt_status
    account = await store.fetch_one("SELECT * FROM billing_accounts WHERE user_id=?", (USER,))
    assert account["needs_review"] is False


async def test_npd_retry_preserves_original_request_after_configuration_change(billing):
    store, provider = billing
    original_settings = provider.settings
    use_npd(provider)
    provider.timeout_once = True
    quote = await service.quote(USER, "plus")
    request_id = uuid4()
    first = await service.checkout(USER, quote["id"], request_id, "first@example.test", provider)
    assert first["status"] == "creating"
    provider.settings = original_settings
    second = await service.checkout(USER, quote["id"], request_id, "different@example.test", provider)
    assert second["order_id"] == first["order_id"]
    creates = [call for call in provider.calls if call[0] == "POST"]
    assert len(creates) == 2 and creates[0] == creates[1]
    assert "receipt" not in creates[1][3]
    record = await store.fetch_one("SELECT * FROM billing_orders WHERE id=?", (UUID(second["order_id"]),))
    assert record["receipt_mode"] == "npd_manual" and record["receipt_email"] == "first@example.test"
    await service.settle(second["order_id"], provider.paid(second["order_id"]), provider.settings)
    assert (await service.get_order(USER, second["order_id"]))["receipt_status"] == "npd_pending"


@pytest.mark.parametrize("review_reason", ["expired_upgrade", "version_changed", "account_review"])
async def test_verified_capture_needing_access_review_still_requires_receipt(billing, review_reason):
    store, provider = billing
    if review_reason == "expired_upgrade":
        await purchase(billing)
    use_npd(provider)
    _, order = await purchase(billing, plan="pro" if review_reason == "expired_upgrade" else "plus", paid=False)
    if review_reason == "expired_upgrade":
        await store.execute("UPDATE billing_periods SET starts_at=now()-interval '31 days',ends_at=now()-interval '1 second'")
    elif review_reason == "version_changed":
        await store.execute("UPDATE billing_accounts SET version=version+1 WHERE user_id=?", (USER,))
    else:
        await store.execute("UPDATE billing_accounts SET needs_review=TRUE WHERE user_id=?", (USER,))
    captured = provider.paid(order["order_id"])
    await service.settle(order["order_id"], captured, provider.settings)
    await service.settle(order["order_id"], captured, provider.settings)
    record = await store.fetch_one("SELECT * FROM billing_orders WHERE id=?", (UUID(order["order_id"]),))
    assert record["status"] == "review" and record["applied_at"] is None
    assert record["receipt_status"] == "npd_pending"
    queued = await receipts.pending_receipts(user={"tg_id": 999})
    assert [row["order_id"] for row in queued["orders"]] == [order["order_id"]]
    events = await store.fetch_all("SELECT * FROM billing_events WHERE event='npd_receipt_required'")
    assert len(events) == 1
    # An earlier fetched provider snapshot arriving late must not lose the queue.
    stale = {**captured, "status": "pending", "paid": False}
    await service.settle(order["order_id"], stale, provider.settings)
    assert (await service.get_order(USER, order["order_id"]))["receipt_status"] == "npd_pending"
    assert (await service.get_order(USER, order["order_id"]))["status"] == "review"


@pytest.mark.parametrize("change", ["wrong_amount", "wrong_shop", "wrong_user", "test", "not_paid", "pending"])
async def test_unverified_capture_never_enters_npd_queue(billing, change):
    store, provider = billing
    use_npd(provider)
    _, order = await purchase(billing, paid=False)
    value = provider.paid(order["order_id"])
    if change == "wrong_amount":
        value["amount"]["value"] = "1.00"
    elif change == "wrong_shop":
        value["recipient"]["account_id"] = "other"
    elif change == "wrong_user":
        value["metadata"]["user_id"] = "other"
    elif change == "test":
        value["test"] = True
    elif change == "not_paid":
        value["paid"] = False
    else:
        value.update(status="pending", paid=False)
    await service.settle(order["order_id"], value, provider.settings)
    assert (await receipts.pending_receipts(user={"tg_id": 999})) == {"orders": [], "refunds": []}
    assert not await store.fetch_all("SELECT * FROM billing_events WHERE event='npd_receipt_required'")


async def test_npd_refund_before_settlement_records_capture_and_manual_tax_followup_once(billing):
    store, provider = billing
    use_npd(provider)
    _, order = await purchase(billing, paid=False)
    provider.paid(order["order_id"])
    refund = refund_for(provider, order)
    await service.record_refund(refund, provider)
    await service.record_refund(refund, provider)
    record = await service.get_order(USER, order["order_id"])
    assert record["status"] == "refunded" and record["receipt_status"] == "npd_pending"
    assert not await store.fetch_all("SELECT * FROM billing_periods")
    events = await store.fetch_all("SELECT event FROM billing_events WHERE event LIKE 'npd_%' ORDER BY id")
    assert events == [{"event": "npd_receipt_required"}, {"event": "npd_refund_receipt_review"}]
    queue = await receipts.pending_receipts(user={"tg_id": 999})
    assert queue["orders"] == [] and len(queue["refunds"]) == 1
    assert queue["refunds"][0]["status"] == "refunded"
    assert queue["refunds"][0]["amount_rub"] == queue["refunds"][0]["refunded_amount_rub"] == "390.00"
    assert queue["refunds"][0]["requires_manual_review"] is True


async def test_partial_refund_with_attached_receipt_stays_visible_for_tax_correction(billing):
    store, provider = billing
    use_npd(provider)
    _, order = await purchase(billing)
    url = "https://lknpd.nalog.ru/api/v1/receipt/123456789012/200abcDEF9/print"
    await store.execute("UPDATE billing_orders SET receipt_status='npd_attached',npd_receipt_url=? WHERE id=?",
                        (url, UUID(order["order_id"])))
    await service.record_refund(refund_for(provider, order, "100.00"), provider)
    queue = await receipts.pending_receipts(user={"tg_id": 999})
    assert queue["orders"] == [] and len(queue["refunds"]) == 1
    assert queue["refunds"][0]["amount_rub"] == "390.00"
    assert queue["refunds"][0]["refunded_amount_rub"] == "100.00"
    assert queue["refunds"][0]["npd_receipt_url"] == url
    assert queue["refunds"][0]["status"] == "succeeded"
