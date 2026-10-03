"""Administrator attachment of receipts already issued in the official My Tax app.

Saving a link does not issue, verify or send a fiscal receipt. The administrator
must check its customer, description and amount in My Tax before attaching it.
"""
import json
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import db
from app.api.account_auth import verify_csrf, web_user
from app.api.auth import is_admin
from app.core.rate_limit import admit
from .plans import PLANS, money

router = APIRouter(prefix="/api/billing/admin", tags=["billing-receipts"])

# Accept only a direct printable My Tax receipt, without credentials, escapes,
# queries, fragments or alternate hosts. Never fetch the submitted URL.
RECEIPT_URL = re.compile(
    r"https://lknpd\.nalog\.ru/api/v1/receipt/[0-9]{12}/[A-Za-z0-9]{1,64}/print"
)


async def administrator(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    user = await web_user(request)
    if not is_admin(user):
        raise HTTPException(403, "Доступно только администратору.")
    if not admit(("billing-receipts", user["tg_id"], request.method), 60 if request.method == "GET" else 20):
        raise HTTPException(429, "Слишком много запросов. Повторите через минуту.", headers={"Retry-After": "60"})
    return user


async def mutate(request: Request, user=Depends(administrator)) -> dict:
    await verify_csrf(request)
    return user


def _description(order: dict) -> str:
    body = order["request_body"]
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            body = {}
    value = body.get("description") if isinstance(body, dict) else None
    if isinstance(value, str) and value.strip():
        return "".join(character for character in value if character.isprintable())[:128]
    name = order["plan"].upper() if order["plan"] in PLANS else ""
    return f"Доступ к сервису Нейропостинг {name}".strip()


@router.get("/receipts")
async def pending_receipts(user=Depends(administrator)) -> dict:
    rows = await db.fetch_all(
        "SELECT id, amount, request_body, receipt_email, created_at, plan, status FROM billing_orders o "
        "WHERE receipt_mode='npd_manual' AND environment='live' AND receipt_status='npd_pending' "
        "AND npd_receipt_url IS NULL AND NOT EXISTS(SELECT 1 FROM billing_refunds r "
        "WHERE r.order_id=o.id AND r.status='succeeded') ORDER BY created_at, id LIMIT 100"
    )
    refunds = await db.fetch_all(
        "SELECT o.id,o.amount,o.receipt_email,o.created_at,o.plan,o.status,o.npd_receipt_url,r.returned "
        "FROM billing_orders o JOIN (SELECT order_id,SUM(amount) AS returned,MAX(checked_at) AS refund_at "
        "FROM billing_refunds WHERE status='succeeded' GROUP BY order_id) r ON r.order_id=o.id "
        "WHERE o.receipt_mode='npd_manual' AND o.environment='live' "
        "AND o.receipt_status IN ('npd_pending','npd_attached') ORDER BY r.refund_at DESC,o.id LIMIT 100"
    )
    return {"orders": [{"order_id": str(row["id"]), "amount_rub": money(row["amount"]),
                        "description": _description(row), "receipt_email": row["receipt_email"],
                        "created_at": row["created_at"], "plan": row["plan"], "status": row["status"]} for row in rows],
            "refunds": [{"order_id": str(row["id"]), "amount_rub": money(row["amount"]),
                         "refunded_amount_rub": money(row["returned"]), "npd_receipt_url": row["npd_receipt_url"],
                         "receipt_email": row["receipt_email"], "created_at": row["created_at"],
                         "plan": row["plan"], "status": row["status"], "requires_manual_review": True}
                        for row in refunds]}


class ReceiptBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    receipt_url: str = Field(min_length=1, max_length=256)

    @field_validator("receipt_url")
    @classmethod
    def official_receipt(cls, value: str) -> str:
        if not RECEIPT_URL.fullmatch(value):
            raise ValueError("Нужна прямая ссылка на чек из приложения «Мой налог» (lknpd.nalog.ru).")
        return value


@router.post("/receipts/{order_id}")
async def attach_receipt(order_id: UUID, body: ReceiptBody, user=Depends(mutate)) -> dict:
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        order = await conn.fetchrow("SELECT * FROM billing_orders WHERE id=$1 FOR UPDATE", order_id)
        if not order:
            raise HTTPException(404, "Платёж не найден.")
        if (order["receipt_mode"] != "npd_manual" or order["environment"] != "live"
                or order["receipt_status"] not in ("npd_pending", "npd_attached")):
            raise HTTPException(409, "Чек можно прикрепить только к подтверждённой оплате с чеком НПД.")
        if order["npd_receipt_url"]:
            if order["npd_receipt_url"] != body.receipt_url:
                raise HTTPException(409, "К этой оплате уже прикреплён другой чек.")
            attached_at = order["npd_receipt_attached_at"]
        else:
            attached_at = db.utcnow()
            await conn.execute(
                "UPDATE billing_orders SET npd_receipt_url=$2, npd_receipt_attached_at=$3, "
                "receipt_status='npd_attached' WHERE id=$1",
                order_id, body.receipt_url, attached_at,
            )
            await conn.execute(
                "INSERT INTO billing_events(order_id,event) VALUES($1,$2)",
                order_id, f"npd_receipt_attached:admin={user['tg_id']}",
            )
    return {"order_id": str(order_id), "npd_receipt_url": body.receipt_url,
            "npd_receipt_attached_at": attached_at}
