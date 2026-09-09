import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.sources import safe_http


@pytest.mark.parametrize('url', [
    'http://127.0.0.1', 'http://10.1.2.3', 'http://169.254.169.254',
    'http://[::1]', 'http://[::ffff:8.8.8.8]', 'file:///etc/passwd',
    'http://user:secret@example.com', 'http://example.com:8080',
    'http://localhost.', 'http://[fe80::1%25eth0]', 'http://224.0.0.1',
])
def test_unsafe_urls(url):
    with pytest.raises(ValueError):
        safe_http.validate_url(url)


@pytest.mark.asyncio
async def test_dns_is_pinned(monkeypatch):
    resolver = AsyncMock(return_value=[(2, 1, 6, '', ('8.8.8.8', 443))])
    connect = AsyncMock(return_value=object())
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolver)
    monkeypatch.setattr(safe_http.AnyIOBackend, 'connect_tcp', connect)
    await safe_http.PublicNetworkBackend().connect_tcp('example.com', 443)
    assert connect.call_args.args[:2] == ('8.8.8.8', 443)
    assert resolver.await_count == 1


@pytest.mark.asyncio
async def test_mixed_dns_never_connects(monkeypatch):
    resolver = AsyncMock(return_value=[(2, 1, 6, '', (ip, 443)) for ip in ('8.8.8.8', '127.0.0.1')])
    connect = AsyncMock()
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolver)
    monkeypatch.setattr(safe_http.AnyIOBackend, 'connect_tcp', connect)
    with pytest.raises(safe_http.UnsafeURL):
        await safe_http.PublicNetworkBackend().connect_tcp('example.com', 443)
    connect.assert_not_called()


class Response:
    def __init__(self, status=200, headers=(), chunks=(b'ok',), delay=0):
        self.status, self.headers, self.chunks, self.delay = status, headers, chunks, delay

    async def aiter_stream(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk


class Pool:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []
        self.closed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        self.urls.append(url)
        try:
            yield next(self.responses)
        finally:
            self.closed += 1


def pool_for(monkeypatch, *responses):
    pool = Pool(responses)
    monkeypatch.setattr(safe_http.httpcore, 'AsyncConnectionPool', lambda **kwargs: pool)
    return pool


@pytest.mark.asyncio
async def test_redirect_to_private_is_blocked(monkeypatch):
    pool = pool_for(monkeypatch, Response(302, [(b'location', b'http://127.0.0.1/admin')]))
    with pytest.raises(safe_http.UnsafeURL):
        await safe_http.fetch('https://example.com')
    assert len(pool.urls) == pool.closed == 1


@pytest.mark.asyncio
async def test_relative_redirect(monkeypatch):
    pool = pool_for(monkeypatch, Response(302, [(b'location', b'/article')]), Response())
    result = await safe_http.fetch('https://example.com')
    assert result.content == b'ok'
    assert pool.urls[-1] == 'https://example.com/article'
    assert pool.closed == 2


@pytest.mark.parametrize('response', [
    Response(chunks=(b'123', b'456')),
    Response(headers=[(b'content-length', b'100')]),
    Response(headers=[(b'content-encoding', b'gzip')]),
])
@pytest.mark.asyncio
async def test_body_limits(monkeypatch, response):
    pool = pool_for(monkeypatch, response)
    with pytest.raises(safe_http.UnsafeURL):
        await safe_http.fetch('https://example.com', max_bytes=4)
    assert pool.closed == 1


@pytest.mark.asyncio
async def test_total_deadline(monkeypatch):
    pool = pool_for(monkeypatch, Response(delay=1))
    with pytest.raises(TimeoutError):
        await safe_http.fetch('https://example.com', total_timeout=.05)
    assert pool.closed == 1


@pytest.mark.asyncio
async def test_redirect_limit(monkeypatch):
    pool = pool_for(monkeypatch, *[Response(302, [(b'location', b'/again')])] * 3)
    with pytest.raises(safe_http.UnsafeURL):
        await safe_http.fetch('https://example.com', max_redirects=2)
    assert len(pool.urls) == pool.closed == 3


@pytest.mark.asyncio
async def test_rss_parser_receives_bytes(monkeypatch):
    import httpx
    from app.sources import rss
    content = b'<rss version="2.0"><channel><title>News</title><item><title>Hello</title><link>https://example.com/1</link></item></channel></rss>'
    monkeypatch.setattr(rss, 'fetch_public', AsyncMock(return_value=httpx.Response(
        200, content=content, request=httpx.Request('GET', 'https://example.com/feed'))))
    items, title = await rss.fetch('https://example.com/feed')
    assert title == 'News'
    assert items[0].text == 'Hello'


@pytest.mark.asyncio
async def test_content_type_rejected(monkeypatch):
    pool = pool_for(monkeypatch, Response(headers=[(b'content-type', b'application/octet-stream')]))
    with pytest.raises(safe_http.UnsafeURL):
        await safe_http.fetch('https://example.com', allowed_types=('text/html',))
    assert pool.closed == 1
