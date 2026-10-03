import asyncio

import httpx
import pytest
from starlette.responses import PlainTextResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.ingress import IngressMiddleware, TokenBuckets, client_key


def test_refill_is_continuous_without_minute_boundary_burst():
    now = [0.0]
    buckets = TokenBuckets(1, 2, clock=lambda: now[0])
    assert buckets.admit('a') and buckets.admit('a')
    assert not buckets.admit('a')
    now[0] = 0.99
    assert not buckets.admit('a')
    now[0] = 1.0
    assert buckets.admit('a')
    assert not buckets.admit('a')
    assert buckets.admit('b')


def test_capacity_does_not_evict_active_debt_and_recovers_after_idle():
    now = [0.0]
    buckets = TokenBuckets(1, 1, capacity=2, clock=lambda: now[0])
    assert buckets.admit('a') and buckets.admit('b')
    assert not buckets.admit('c')
    assert not buckets.admit('a')
    assert len(buckets.entries) == 2
    now[0] = 61
    assert buckets.admit('c')


def test_ipv6_rotating_host_addresses_share_budget():
    assert client_key({'client': ('2001:db8::1', 1)}) == client_key({'client': ('2001:db8::2', 2)})
    assert client_key({'client': ('::ffff:192.0.2.1', 1)}) == '192.0.2.1'


async def ok(scope, receive, send):
    await PlainTextResponse('ok')(scope, receive, send)


@pytest.mark.asyncio
async def test_rejects_before_reading_body_or_calling_application():
    calls = []
    async def endpoint(scope, receive, send):
        calls.append(scope['path'])
        await ok(scope, receive, send)
    app = IngressMiddleware(endpoint, per_ip_burst=1, clock=lambda: 0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as c:
        assert (await c.get('/api/bootstrap')).status_code == 200
        async def body():
            raise AssertionError('Rejected request body must not be consumed')
            yield b''
        r = await c.post('/api/channels', content=body())
        assert r.status_code == 429 and r.headers['Retry-After'] == '5'
    assert calls == ['/api/bootstrap']


@pytest.mark.asyncio
async def test_untrusted_forwarded_headers_cannot_reset_budget():
    app = ProxyHeadersMiddleware(IngressMiddleware(ok, per_ip_burst=1, clock=lambda: 0),
                                 trusted_hosts='172.30.90.2')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=('192.0.2.1', 1)), base_url='http://test') as c:
        assert (await c.get('/', headers={'X-Forwarded-For': '198.51.100.1'})).status_code == 200
        assert (await c.get('/', headers={'X-Forwarded-For': '198.51.100.2'})).status_code == 429


@pytest.mark.asyncio
async def test_trusted_proxy_preserves_separate_visitor_budgets():
    app = ProxyHeadersMiddleware(IngressMiddleware(ok, per_ip_burst=1, clock=lambda: 0),
                                 trusted_hosts='172.30.90.2')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=('172.30.90.2', 1)), base_url='http://test') as c:
        for ip in ('198.51.100.1', '198.51.100.2'):
            assert (await c.get('/', headers={'X-Forwarded-For': ip})).status_code == 200
            assert (await c.get('/', headers={'X-Forwarded-For': ip})).status_code == 429


@pytest.mark.asyncio
async def test_global_budget_limits_many_source_addresses():
    app = IngressMiddleware(ok, global_burst=1, clock=lambda: 0)
    for ip, status in [('192.0.2.1', 200), ('192.0.2.2', 429)]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=(ip, 1)), base_url='http://test') as c:
            assert (await c.get('/')).status_code == status


@pytest.mark.asyncio
async def test_concurrency_releases_on_cancellation():
    started = asyncio.Event()
    async def slow(scope, receive, send):
        started.set()
        await asyncio.Event().wait()
    app = IngressMiddleware(slow, max_inflight=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as c:
        task = asyncio.create_task(c.get('/'))
        await started.wait()
        assert (await c.get('/')).status_code == 503
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.inflight == 0


@pytest.mark.asyncio
async def test_concurrency_releases_on_exception():
    async def broken(scope, receive, send):
        raise ValueError('test')
    app = IngressMiddleware(broken)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as c:
        with pytest.raises(ValueError):
            await c.get('/')
        assert app.inflight == 0
