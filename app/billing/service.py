"""Durable orders, immutable quotes, verified settlement and account-wide access."""
import asyncio
import json
import re
from datetime import timedelta
from uuid import UUID, uuid4
from contextlib import asynccontextmanager

from app import db
from .plans import PLANS, money, month_after, prorated_upgrade
from .provider import Settings, YooKassa, ProviderError, amount_kopecks, safe_confirmation


class BillingError(ValueError):
    pass


def as_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise BillingError('Некорректный номер операции') from None


async def account_lock(conn, user_id, *, allow_blocked=False):
    # Same user-row lock as promo redemption and publication quota reservation.
    user = await conn.fetchrow('SELECT * FROM users WHERE tg_id=$1 FOR UPDATE', user_id)
    if not user or (user['blocked'] and not allow_blocked):
        raise BillingError('Пользователь недоступен')
    await conn.execute('INSERT INTO billing_accounts(user_id) VALUES($1) ON CONFLICT DO NOTHING',user_id)
    account = await conn.fetchrow('SELECT * FROM billing_accounts WHERE user_id=$1 FOR UPDATE',user_id)
    return dict(user), dict(account)


async def active_period(conn, user_id, now):
    row = await conn.fetchrow('SELECT * FROM billing_periods WHERE user_id=$1 AND NOT revoked '
                              'AND starts_at<=$2 AND ends_at>$2 ORDER BY starts_at DESC LIMIT 1',user_id,now)
    return dict(row) if row else None


async def sync_entitlement(conn, user_id, now=None):
    now = now or db.utcnow()
    period = await active_period(conn,user_id,now)
    account = await conn.fetchrow('SELECT * FROM billing_accounts WHERE user_id=$1',user_id)
    if period:
        if not account['entitlement_active']:
            await conn.execute('UPDATE billing_accounts SET legacy_until=u.access_until,legacy_plan=u.plan, '
                'legacy_daily_limit=u.daily_limit,entitlement_active=TRUE FROM users u '
                'WHERE billing_accounts.user_id=$1 AND u.tg_id=$1',user_id)
        await conn.execute('UPDATE users SET plan=$2,daily_limit=$3,access_until=$4 WHERE tg_id=$1',
                           user_id,period['plan'],PLANS[period['plan']].ai_daily_limit,period['ends_at'])
    elif account and account['entitlement_active']:
        await conn.execute('UPDATE users SET access_until=$2,plan=$3,daily_limit=$4 WHERE tg_id=$1',
            user_id,account['legacy_until'],account['legacy_plan'],account['legacy_daily_limit'])
        await conn.execute('UPDATE billing_accounts SET entitlement_active=FALSE WHERE user_id=$1',user_id)
    return period


async def sync_user(user_id):
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        # Do not create billing accounts for legacy users just because they browse.
        if await conn.fetchval('SELECT 1 FROM billing_accounts b JOIN users u ON u.tg_id=b.user_id WHERE b.user_id=$1 AND u.blocked=0',user_id):
            await account_lock(conn,user_id)
            await sync_entitlement(conn,user_id)


async def subscription(user_id):
    await sync_user(user_id)
    pool = await db.connect()
    async with pool.acquire() as conn:
        period = await active_period(conn,user_id,db.utcnow())
        account = await conn.fetchrow('SELECT * FROM billing_accounts WHERE user_id=$1',user_id)
        user = await conn.fetchrow('SELECT * FROM users WHERE tg_id=$1',user_id)
        future = await conn.fetchrow('SELECT * FROM billing_periods WHERE user_id=$1 AND NOT revoked '
                                    'AND starts_at>now() ORDER BY starts_at LIMIT 1',user_id)
    from app.core.publisher import used_today
    used = await used_today(user_id)
    from app.api.auth import has_access
    return {'current_plan':period['plan'] if period else None,
            'status':'active' if period or has_access(dict(user)) else 'inactive',
            'access_source':'subscription' if period else 'code' if has_access(dict(user)) else None,
            'period_start':period['starts_at'] if period else None,
            'period_end':period['ends_at'] if period else user['access_until'],
            'ai_daily_limit':PLANS[period['plan']].ai_daily_limit if period else user['daily_limit'],
            'ai_used_today':used,
            'scheduled_plan':future['plan'] if future else account['scheduled_plan'] if account else None,
            'scheduled_at':future['starts_at'] if future else account['scheduled_at'] if account else None,
            'next_period_paid':bool(future),'auto_renew':False,
            'needs_review':bool(account and account['needs_review'])}


async def quote(user_id, plan_code):
    if plan_code not in PLANS:
        raise BillingError('Выберите доступный тариф')
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        _, account = await account_lock(conn,user_id)
        if account['needs_review']:
            raise BillingError('Изменение подписки временно требует проверки поддержки.')
        now=db.utcnow(); current=await active_period(conn,user_id,now); target=PLANS[plan_code]
        future = await conn.fetchrow('SELECT * FROM billing_periods WHERE user_id=$1 AND NOT revoked AND starts_at>$2 LIMIT 1',user_id,now)
        if future and (not current or target.price<=PLANS[current['plan']].price):
            raise BillingError('Следующий период уже оплачен. Смена тарифа требует сверки с поддержкой.')
        if current and target.price > PLANS[current['plan']].price:
            kind='upgrade'; start=now; end=current['ends_at']
            amount=prorated_upgrade(PLANS[current['plan']],target,current['starts_at'],end,now)
        elif current and target.price < PLANS[current['plan']].price:
            kind='downgrade'; start=current['ends_at']; end=month_after(start); amount=0
        else:
            kind='renewal' if current else 'new'
            start=current['ends_at'] if current else now; end=month_after(start); amount=target.price
        # Scheduled downgrade is purchased at the next renewal, without autocharge.
        q={'id':uuid4(),'user_id':user_id,'plan':plan_code,'kind':kind,'amount':amount,
           'account_version':account['version'],'period_id':current['id'] if current else None,
           'period_start':start,'period_end':end,'expires_at':now+timedelta(minutes=10)}
        await conn.execute('INSERT INTO billing_quotes(id,user_id,plan,kind,amount,account_version,period_id,period_start,period_end,expires_at) '
                           'VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)',*q.values())
        return q


def public_quote(q, available):
    return {'quote_id':str(q['id']),'plan':q['plan'],'amount_rub':money(q['amount']),
            'effective_at':q['period_start'],'period_end':q['period_end'],
            'change_type':q['kind'],'expires_at':q['expires_at'],
            'checkout_available':q['kind']=='downgrade' or available,
            'unavailable_reason':None if available or q['kind']=='downgrade' else 'Оплата пока не подключена'}


def public_order(row):
    return {'order_id':str(row['id']),'plan':row['plan'],'status':row['status'],
            'amount_rub':money(row['amount']),'created_at':row['created_at'],
            'confirmation_url':row['confirmation_url'] if row['status']=='pending' else None,
            'test':row['environment']=='test','receipt_status':row['receipt_status'],
            'receipt_mode':row['receipt_mode'],'npd_receipt_url':row['npd_receipt_url']}


async def checkout(user_id, quote_id, request_id, email, provider=None):
    provider=provider or YooKassa(); cfg=provider.settings
    quote_id=as_uuid(quote_id); request_id=as_uuid(request_id)
    pool=await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        _, account=await account_lock(conn,user_id)
        if account['needs_review']:
            raise BillingError('Изменение подписки временно требует проверки поддержки.')
        existing=await conn.fetchrow('SELECT * FROM billing_orders WHERE user_id=$1 AND request_id=$2',user_id,request_id)
        if existing:
            if existing['quote_id'] != quote_id:
                raise BillingError('Номер попытки уже относится к другому расчёту')
            order=dict(existing)
        else:
            q=await conn.fetchrow('SELECT * FROM billing_quotes WHERE id=$1 AND user_id=$2',quote_id,user_id)
            if not q: raise BillingError('Расчёт не найден')
            existing=await conn.fetchrow('SELECT * FROM billing_orders WHERE quote_id=$1',quote_id)
            if existing:
                order=dict(existing)
            else:
                now=db.utcnow()
                if (q['kind']=='downgrade' and account['scheduled_plan']==q['plan']
                        and account['scheduled_at']==q['period_start']):
                    return {'order_id':None,'status':'scheduled','confirmation_url':None,
                            'plan':q['plan'],'effective_at':q['period_start']}
                if q['expires_at']<=now or q['account_version'] != account['version']:
                    raise BillingError('Расчёт устарел. Рассчитайте стоимость снова.')
                if q['kind'] in ('upgrade','downgrade'):
                    current=await active_period(conn,user_id,now)
                    if not current or current['id']!=q['period_id']:
                        raise BillingError('Период закончился. Рассчитайте стоимость снова.')
                if await conn.fetchval("SELECT 1 FROM billing_orders WHERE user_id=$1 AND status IN ('creating','pending','review')",user_id):
                    raise BillingError('Предыдущий платёж ещё проверяется. Откройте его в истории.')
                if q['kind']=='downgrade':
                    await conn.execute('UPDATE billing_accounts SET scheduled_plan=$2,scheduled_at=$3,version=version+1 WHERE user_id=$1',user_id,q['plan'],q['period_start'])
                    return {'order_id':None,'status':'scheduled','confirmation_url':None,'plan':q['plan'],'effective_at':q['period_start']}
                if not cfg.ready(): raise BillingError('Оплата пока не подключена')
                if not isinstance(email,str) or len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
                    raise BillingError('Укажите email для чека')
                ident=uuid4()
                description=f"NeuroPost {q['plan'].upper()} — {'доплата за повышение тарифа' if q['kind']=='upgrade' else 'месяц доступа'}"
                body={'amount':{'value':money(q['amount']),'currency':'RUB'},'capture':True,
                      'confirmation':{'type':'redirect','return_url':cfg.origin+'/account?order='+str(ident)},
                      'description':description,'metadata':{'order_id':str(ident),'user_id':str(user_id)}}
                if cfg.receipt_mode=='yookassa_54fz':
                    body['receipt']={'customer':{'email':email},'items':[{'description':description,'quantity':'1.00',
                        'amount':{'value':money(q['amount']),'currency':'RUB'},'vat_code':cfg.vat_code,
                        'payment_subject':cfg.payment_subject,'payment_mode':cfg.payment_mode}]}
                row=await conn.fetchrow('INSERT INTO billing_orders(id,user_id,quote_id,request_id,plan,kind,amount,account_version,period_id,period_start,period_end,environment,request_body,receipt_mode,receipt_email) '
                     'VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14,$15) RETURNING *',
                     ident,user_id,quote_id,request_id,q['plan'],q['kind'],q['amount'],q['account_version'],q['period_id'],q['period_start'],q['period_end'],cfg.environment,json.dumps(body),cfg.receipt_mode,email)
                order=dict(row)
    await refresh_order(order['id'],provider)
    return await get_order(user_id,order['id'])


async def get_order(user_id,order_id):
    row=await db.fetch_one('SELECT * FROM billing_orders WHERE id=? AND user_id=?',(as_uuid(order_id),user_id))
    if not row: raise BillingError('Платёж не найден')
    return public_order(row)


async def refresh_order(order_id,provider=None):
    provider=provider or YooKassa(); pool=await db.connect(); ident=as_uuid(order_id)
    if not provider.settings.ready(): return
    async with pool.acquire() as lease:
        # Session lock serializes HTTP retries even across multiple worker processes.
        locked=await lease.fetchval('SELECT pg_try_advisory_lock(hashtextextended($1,0))','billing:'+str(ident))
        if not locked: return
        try:
            row=await lease.fetchrow('SELECT * FROM billing_orders WHERE id=$1',ident)
            if not row or row['status'] not in ('creating','pending'): return
            if row['environment']!=provider.settings.environment: return
            if row['provider_id']:
                value=await provider.request('GET','payments/'+row['provider_id'])
            else:
                if db.utcnow()-row['created_at']>=timedelta(hours=23):
                    changed=await lease.execute("UPDATE billing_orders SET status='review' WHERE id=$1 "
                        "AND status='creating' AND provider_id IS NULL AND applied_at IS NULL",ident)
                    if changed=='UPDATE 1': report_review(ident)
                    return
                body=row['request_body']; body=json.loads(body) if isinstance(body,str) else body
                value=await provider.request('POST','payments',body=body,key=str(ident))
            await settle(ident,value,provider.settings,_conn=lease)
        except ProviderError:
            # Unknown outcome keeps its durable order/key and blocks another purchase.
            await lease.execute('UPDATE billing_orders SET checked_at=now() WHERE id=$1',ident)
        finally:
            await lease.execute('SELECT pg_advisory_unlock(hashtextextended($1,0))','billing:'+str(ident))


@asynccontextmanager
async def connection(existing=None):
    if existing is not None:
        yield existing
    else:
        pool=await db.connect()
        async with pool.acquire() as conn:
            yield conn


async def mark_npd_capture(conn, order):
    """Called only after verifying an actual live capture, with the order locked.

    Receipt duties follow received money even when granting access needs review.
    The NPD states are durable capture evidence, not the provider's 54-FZ status.
    """
    if order['receipt_mode']!='npd_manual' or order['environment']!='live':
        return
    if order['receipt_status'] in ('npd_pending','npd_attached'):
        return
    status='npd_attached' if order['npd_receipt_url'] else 'npd_pending'
    await conn.execute('UPDATE billing_orders SET receipt_status=$2 WHERE id=$1',order['id'],status)
    await conn.execute("INSERT INTO billing_events(order_id,event) VALUES($1,'npd_receipt_required')",order['id'])


async def settle(order_id,value,cfg,*,_conn=None):
    ident=as_uuid(order_id)
    # Reuse refresh's lease so concurrent requests cannot exhaust the pool while
    # each waits for a second connection. Owner precedes order in all lock paths.
    async with connection(_conn) as conn,conn.transaction():
        owner=await conn.fetchval('SELECT user_id FROM billing_orders WHERE id=$1',ident)
        if not owner: raise BillingError('Платёж не найден')
        user,account=await account_lock(conn,owner,allow_blocked=True)
        order=dict(await conn.fetchrow('SELECT * FROM billing_orders WHERE id=$1 FOR UPDATE',ident))
        if order['applied_at'] or order['status'] in ('succeeded','test_succeeded','refunded'):
            return
        try:
            remote_id,confirmation=verify_payment(order,value,cfg)
        except (ValueError,TypeError,AttributeError):
            await conn.execute("UPDATE billing_orders SET status='review',checked_at=now() WHERE id=$1",ident)
            await conn.execute("INSERT INTO billing_events(order_id,event) VALUES($1,'verification_mismatch')",ident)
            report_review(ident)
            return
        status=value.get('status')
        # A delayed pending snapshot must not erase previously verified capture.
        if order['receipt_status'] in ('npd_pending','npd_attached') and status!='succeeded':
            return
        await conn.execute("UPDATE billing_orders SET provider_id=$2,confirmation_url=$3,"
                           "receipt_status=CASE WHEN receipt_mode='yookassa_54fz' THEN $4 ELSE receipt_status END,"
                           'checked_at=now() WHERE id=$1',
                           ident,remote_id,confirmation,value.get('receipt_registration'))
        if status=='canceled':
            await conn.execute("UPDATE billing_orders SET status='canceled' WHERE id=$1",ident); return
        if status not in ('pending','waiting_for_capture','succeeded') or (status=='succeeded' and value.get('paid') is not True):
            await conn.execute("UPDATE billing_orders SET status='review' WHERE id=$1",ident)
            report_review(ident)
            return
        if status!='succeeded':
            await conn.execute("UPDATE billing_orders SET status='pending' WHERE id=$1",ident); return
        if order['environment']=='test':
            await conn.execute("UPDATE billing_orders SET status='test_succeeded',applied_at=now() WHERE id=$1",ident)
            return  # Sandbox payments never grant real service.
        await mark_npd_capture(conn,order)
        if account['version']!=order['account_version'] or account['needs_review']:
            await conn.execute("UPDATE billing_orders SET status='review' WHERE id=$1",ident)
            report_review(ident)
            return
        now=db.utcnow()
        if order['kind']=='upgrade':
            period=await conn.fetchrow('SELECT * FROM billing_periods WHERE id=$1 AND user_id=$2 AND NOT revoked FOR UPDATE',order['period_id'],owner)
            if not period or period['ends_at']<=now:
                await conn.execute("UPDATE billing_orders SET status='review' WHERE id=$1",ident)
                report_review(ident)
                return
            await conn.execute('UPDATE billing_periods SET plan=$2 WHERE id=$1',period['id'],order['plan'])
        else:
            start=max(order['period_start'],now)
            end=month_after(start)
            if await conn.fetchval('SELECT 1 FROM billing_periods WHERE user_id=$1 AND NOT revoked '
                'AND starts_at<$3 AND ends_at>$2',owner,start,end):
                await conn.execute("UPDATE billing_orders SET status='review' WHERE id=$1",ident)
                return
            await conn.execute('INSERT INTO billing_periods(id,user_id,plan,starts_at,ends_at,order_id) VALUES($1,$2,$3,$4,$5,$6)',
                               uuid4(),owner,order['plan'],start,end,ident)
        await conn.execute("UPDATE billing_orders SET status='succeeded',applied_at=now() WHERE id=$1",ident)
        await conn.execute('UPDATE billing_accounts SET version=version+1,scheduled_plan=NULL,scheduled_at=NULL WHERE user_id=$1',owner)
        await sync_entitlement(conn,owner,now)
        await conn.execute("INSERT INTO billing_events(order_id,event) VALUES($1,'entitlement_applied')",ident)


def report_review(order_id):
    import logging
    logging.getLogger('billing').error('Платёж %s требует ручной сверки в ЮKassa',order_id)


def verify_payment(order,value,cfg):
    remote_id=str(as_uuid(value.get('id')))
    if (order['provider_id'] and order['provider_id']!=remote_id
        or value.get('test') is not (order['environment']=='test')
        or order['environment']!=cfg.environment
        or amount_kopecks(value.get('amount'))!=order['amount']
        or value.get('recipient',{}).get('account_id')!=cfg.shop_id
        or value.get('metadata',{}).get('order_id')!=str(order['id'])
        or value.get('metadata',{}).get('user_id')!=str(order['user_id'])):
        raise ValueError('payment mismatch')
    return remote_id,safe_confirmation(value.get('confirmation',{}).get('confirmation_url'))


async def record_refund(value,provider):
    # Only called with a refund fetched from the fixed authenticated provider API.
    try:
        refund_id=str(as_uuid(value.get('id')))
        payment_id=str(as_uuid(value.get('payment_id')))
        amount=amount_kopecks(value.get('amount'))
        if value.get('status')!='succeeded' or amount<=0: return
    except (ValueError,TypeError,AttributeError):
        raise ProviderError('Некорректный возврат') from None
    order=await db.fetch_one('SELECT * FROM billing_orders WHERE provider_id=?',(payment_id,))
    payment=await provider.request('GET','payments/'+payment_id)
    if not order:
        try:
            ident=as_uuid((payment.get('metadata') or {}).get('order_id'))
        except BillingError:
            return
        order=await db.fetch_one('SELECT * FROM billing_orders WHERE id=?',(ident,))
    if not order: return
    try:
        verify_payment(order,payment,provider.settings)
        if amount>order['amount']: raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise ProviderError('Возврат не подтверждён') from None
    pool=await db.connect()
    async with pool.acquire() as conn,conn.transaction():
        await account_lock(conn,order['user_id'],allow_blocked=True)
        # Settlement may have finished while the provider request was in flight.
        order=dict(await conn.fetchrow('SELECT * FROM billing_orders WHERE id=$1 FOR UPDATE',order['id']))
        if payment.get('status')=='succeeded' and payment.get('paid') is True:
            await mark_npd_capture(conn,order)
        inserted=await conn.fetchval('INSERT INTO billing_refunds(provider_id,order_id,amount,status) '
            "VALUES($1,$2,$3,'succeeded') ON CONFLICT DO NOTHING RETURNING provider_id",refund_id,order['id'],amount)
        if not inserted: return
        total=await conn.fetchval('SELECT SUM(amount) FROM billing_refunds WHERE order_id=$1',order['id'])
        linked_upgrade=await conn.fetchval("SELECT 1 FROM billing_orders u JOIN billing_periods p ON p.id=u.period_id "
            "WHERE p.order_id=$1 AND u.kind='upgrade' AND u.applied_at IS NOT NULL AND u.status='succeeded'",order['id'])
        if total==order['amount'] and order['kind']!='upgrade' and not linked_upgrade:
            await conn.execute('UPDATE billing_periods SET revoked=TRUE WHERE order_id=$1',order['id'])
            await conn.execute("UPDATE billing_orders SET status='refunded' WHERE id=$1",order['id'])
        # Partial refunds and refunded upgrades cannot safely recalculate subsequent
        # upgrades. Keep their existing period until a human resolves the ledger.
        else:
            await conn.execute('UPDATE billing_accounts SET needs_review=TRUE WHERE user_id=$1',order['user_id'])
            if not order['applied_at']:
                await conn.execute("UPDATE billing_orders SET status='review' WHERE id=$1",order['id'])
            report_review(order['id'])
        await conn.execute('UPDATE billing_accounts SET version=version+1 WHERE user_id=$1',order['user_id'])
        await sync_entitlement(conn,order['user_id'])
        await conn.execute("INSERT INTO billing_events(order_id,event) VALUES($1,'refund_verified')",order['id'])
        if order['receipt_mode']=='npd_manual' and order['environment']=='live':
            await conn.execute("INSERT INTO billing_events(order_id,event) VALUES($1,'npd_refund_receipt_review')",order['id'])
            import logging
            logging.getLogger('billing').error('Возврат по платежу %s: проверьте аннулирование или корректировку чека в «Мой налог»',order['id'])


async def audit_order(order_id,provider=None):
    provider=provider or YooKassa()
    order=await db.fetch_one('SELECT * FROM billing_orders WHERE id=?',(as_uuid(order_id),))
    if not order or not order['provider_id'] or order['environment']!=provider.settings.environment: return
    try:
        value=await provider.request('GET','payments/'+order['provider_id'])
        verify_payment(order,value,provider.settings)
        if order['receipt_mode']=='yookassa_54fz':
            await db.execute('UPDATE billing_orders SET receipt_status=?,checked_at=now() WHERE id=?',
                             (value.get('receipt_registration'),order['id']))
        else:
            await db.execute('UPDATE billing_orders SET checked_at=now() WHERE id=?',(order['id'],))
        returned=amount_kopecks(value['refunded_amount']) if value.get('refunded_amount') else 0
        known=await db.fetch_one('SELECT COALESCE(SUM(amount),0) AS total FROM billing_refunds WHERE order_id=?',(order['id'],))
        if returned>known['total'] or (order['receipt_mode']=='yookassa_54fz' and value.get('receipt_registration')=='canceled'):
            changed=await db.update('UPDATE billing_accounts SET needs_review=TRUE WHERE user_id=? AND NOT needs_review',(order['user_id'],))
            if changed: report_review(order['id'])
    except (ProviderError,ValueError,TypeError,AttributeError):
        # Keep trying; never erase a known successful payment on a network failure.
        return


async def reconcile_once(provider=None):
    cfg=(provider or YooKassa()).settings
    rows=await db.fetch_all("SELECT id FROM billing_orders WHERE status IN ('creating','pending') AND environment=? "
        "ORDER BY checked_at NULLS FIRST,created_at LIMIT 30",(cfg.environment,))
    for row in rows:
        await refresh_order(row['id'],provider)
    if cfg.ready():
        settled=await db.fetch_all("SELECT id FROM billing_orders WHERE status IN ('succeeded','test_succeeded') "
            "AND environment=? AND (checked_at IS NULL OR checked_at<now()-interval '1 hour') "
            "ORDER BY checked_at NULLS FIRST LIMIT 30",(cfg.environment,))
        for row in settled:
            await audit_order(row['id'],provider)
    # Activate prepaid next periods even if the browser/webhook never returns.
    users=await db.fetch_all('SELECT user_id FROM billing_accounts')
    for user in users:
        await sync_user(user['user_id'])


async def run():
    import logging
    while True:
        try:
            # Local entitlement expiry/activation works even if the provider is off.
            await reconcile_once()
        except Exception:
            logging.getLogger('billing').error('Сверка платежей не завершена; требуется проверка',exc_info=False)
        await asyncio.sleep(60)
