"""Browser-only checkout; Telegram Mini App may read acquired access only."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.api.account_auth import web_user, verify_csrf
from app.api.auth import current_user
from app.core.rate_limit import admit
from . import service
from .plans import PLANS
from .provider import Settings, YooKassa, ProviderError

router = APIRouter(prefix='/api/billing', tags=['billing'])


async def browser(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    user = await web_user(request)
    # Cookie APIs do not inherit Mini App rate limits.
    if not admit(('billing',user['tg_id'],request.method), 120 if request.method=='GET' else 20):
        raise HTTPException(429,'Слишком много запросов. Повторите через минуту.',headers={'Retry-After':'60'})
    return user


async def mutate(request: Request, user=Depends(browser)):
    await verify_csrf(request)
    return user


@router.get('/catalog')
async def catalog(response: Response):
    response.headers['Cache-Control']='no-store'
    settings=Settings.read()
    return {'plans':[plan.public() for plan in PLANS.values()], 'provider':'yookassa',
            'receipt_mode':settings.receipt_mode,
            'checkout_available':bool(settings.ready()),'test':settings.environment=='test',
            'unavailable_reason':None if settings.ready() else 'Оплата пока не подключена'}


@router.get('/subscription')
async def subscription(request: Request, response: Response):
    response.headers['Cache-Control']='no-store'
    if request.headers.get('x-init-data'):
        user=await current_user(request.headers['x-init-data'],request)
    else:
        user=await browser(request,response)
    return await service.subscription(user['tg_id'])


class QuoteBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    plan: str = Field(min_length=1,max_length=16)


class CheckoutBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    quote_id: str = Field(min_length=36,max_length=36)
    request_id: str = Field(min_length=36,max_length=36)
    receipt_email: str | None = Field(default=None,max_length=254)


@router.post('/quote')
async def quote(body:QuoteBody,user=Depends(mutate)):
    try:
        return service.public_quote(await service.quote(user['tg_id'],body.plan),Settings.read().ready())
    except service.BillingError as exc:
        raise HTTPException(409,str(exc)) from None


@router.post('/checkout')
async def checkout(body:CheckoutBody,user=Depends(mutate)):
    try:
        return await service.checkout(user['tg_id'],body.quote_id,body.request_id,body.receipt_email)
    except service.BillingError as exc:
        raise HTTPException(409,str(exc)) from None


@router.get('/orders')
async def orders(user=Depends(browser)):
    rows=await db.fetch_all('SELECT * FROM billing_orders WHERE user_id=? ORDER BY created_at DESC LIMIT 50',(user['tg_id'],))
    return {'orders':[service.public_order(row) for row in rows]}


@router.get('/orders/{order_id}')
async def order(order_id:UUID,user=Depends(browser)):
    try:
        # Check ownership before any provider request, including refresh.
        await service.get_order(user['tg_id'],order_id)
        await service.refresh_order(order_id)
        return await service.get_order(user['tg_id'],order_id)
    except service.BillingError:
        raise HTTPException(404,'Платёж не найден') from None


@router.post('/webhook')
async def webhook(request:Request):
    try:
        payload=await request.json()
        if not isinstance(payload,dict) or payload.get('type')!='notification':
            raise ValueError()
        event=payload.get('event')
        if event not in ('payment.succeeded','payment.canceled','payment.waiting_for_capture','refund.succeeded'):
            return {'ok':True}
        ident=str(service.as_uuid(payload['object']['id']))
    except (ValueError,TypeError,KeyError):
        raise HTTPException(400,'Некорректное уведомление') from None
    provider=YooKassa()
    try:
        # The body is only a hint. Fetch the actual object from the authenticated API.
        if event=='refund.succeeded':
            value=await provider.request('GET','refunds/'+ident)
            if value.get('id')!=ident:
                raise ProviderError('Некорректное уведомление')
            await service.record_refund(value,provider)
        else:
            value=await provider.request('GET','payments/'+ident)
            if value.get('id')!=ident:
                raise ProviderError('Некорректное уведомление')
            order_id=(value.get('metadata') or {}).get('order_id')
            try:
                order_id=service.as_uuid(order_id)
            except service.BillingError:
                return {'ok':True}  # Another application may share the same shop.
            if await db.fetch_one('SELECT id FROM billing_orders WHERE id=?',(order_id,)):
                await service.settle(order_id,value,provider.settings)
    except ProviderError:
        # Only ACK once persisted; YooKassa retries non-200 responses.
        raise HTTPException(503,'Статус временно не подтверждён') from None
    return {'ok':True}
