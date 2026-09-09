"""Bounded public-web GETs. DNS is checked at the actual TCP connection."""
import asyncio
import ipaddress
import socket
import ssl

import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend


class UnsafeURL(ValueError):
    pass


def public_ip(value: str) -> str:
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_unspecified:
        raise UnsafeURL("адрес внутренней или служебной сети запрещён")
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            raise UnsafeURL("переходные IPv6-адреса запрещены")
    return str(address)


def validate_url(value: str) -> httpx.URL:
    url = httpx.URL(value)
    if (url.scheme not in ("http", "https") or not url.host or url.userinfo
            or url.port not in (None, 80, 443) or "%" in url.host):
        raise UnsafeURL("разрешены только публичные HTTP/HTTPS ссылки без авторизации")
    try:
        ipaddress.ip_address(url.host)
    except ValueError:
        if url.host.rstrip(".").lower() == "localhost":
            raise UnsafeURL("локальный адрес запрещён")
    else:
        public_ip(url.host)
    return url


class PublicNetworkBackend(AnyIOBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        async with asyncio.timeout(timeout or 10):
            answers = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
            # Reject mixed public/private DNS answers, then connect to a literal IP.
            # HTTP Host and TLS SNI remain the original hostname in httpcore.
            addresses = list(dict.fromkeys(public_ip(row[4][0]) for row in answers))
            if not addresses:
                raise UnsafeURL("DNS не вернул адрес")
            last_error = None
            for address in addresses:
                try:
                    return await super().connect_tcp(
                        address, port, timeout, local_address, socket_options
                    )
                except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                    last_error = exc
            raise last_error


async def fetch(url: str, *, max_bytes: int = 2 * 1024 * 1024,
                total_timeout: float = 30, max_redirects: int = 4,
                allowed_types: tuple[str, ...] | None = None) -> httpx.Response:
    """No environment proxies, cookies or credentials; reject compressed responses.

    Identity encoding prevents decompression bombs before an output-size check.
    A single deadline includes DNS, redirects and streamed body reading.
    """
    async with asyncio.timeout(total_timeout):
        async with httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(), network_backend=PublicNetworkBackend(),
            max_connections=1, retries=0,
        ) as pool:
            for hop in range(max_redirects + 1):
                target = validate_url(url)
                async with pool.stream(
                    "GET", str(target),
                    headers={"User-Agent": "NeyroPosting/1.0", "Accept-Encoding": "identity"},
                    extensions={"timeout": {"connect": 10, "read": 10, "write": 10, "pool": 10}},
                ) as response:
                    headers = httpx.Headers(response.headers)
                    if response.status in (301, 302, 303, 307, 308):
                        if hop == max_redirects or not headers.get("location"):
                            raise UnsafeURL("слишком много перенаправлений или отсутствует адрес")
                        url = str(target.join(headers["location"]))
                        continue
                    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if allowed_types and content_type not in allowed_types:
                        raise UnsafeURL("неподдерживаемый тип содержимого")
                    if headers.get("content-encoding", "identity").lower() != "identity":
                        raise UnsafeURL("сервер проигнорировал запрет сжатия ответа")
                    if "content-length" in headers:
                        length = int(headers["content-length"])
                        if length < 0 or length > max_bytes:
                            raise UnsafeURL("превышен размер загрузки")
                    data = bytearray()
                    async for chunk in response.aiter_stream():
                        if len(data) + len(chunk) > max_bytes:
                            raise UnsafeURL("превышен размер загрузки")
                        data.extend(chunk)
                    result = httpx.Response(response.status, headers=headers, content=bytes(data),
                                            request=httpx.Request("GET", target))
                    result.raise_for_status()
                    return result
    raise UnsafeURL("не удалось загрузить ссылку")
