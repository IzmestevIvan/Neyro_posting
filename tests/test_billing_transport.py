"""Exercise real HTTPX/httpcore retries with an offline network backend."""
import json
import ssl

import httpcore
import pytest

from app.billing.provider import ProviderError, Settings, YooKassa


class OfflineStream:
    def __init__(self, backend):
        self.backend = backend
        self.response_sent = False
        self.writes = []

    async def start_tls(self, ssl_context, server_hostname, timeout):
        self.backend.tls.append((ssl_context, server_hostname, timeout))
        if self.backend.fail_tls > 0:
            self.backend.fail_tls -= 1
            raise httpcore.ConnectTimeout("Fixture TLS timeout before any request bytes")
        return self

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def read(self, max_bytes, timeout=None):
        if self.backend.read_timeout:
            raise httpcore.ReadTimeout("Fixture response timeout after request was sent")
        if self.response_sent:
            return b""
        self.response_sent = True
        body = json.dumps({"id": "offline-payment"}).encode()
        return (f"HTTP/1.1 {self.backend.status} Fixture\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body

    async def aclose(self):
        pass

    def get_extra_info(self, name):
        return False if name == "is_readable" else None


class OfflineNetwork:
    def __init__(self, *, fail_connect=0, fail_tls=0, read_timeout=False, status=200):
        self.fail_connect = fail_connect
        self.fail_tls = fail_tls
        self.read_timeout = read_timeout
        self.status = status
        self.connects, self.tls, self.sleeps, self.streams = [], [], [], []

    async def connect_tcp(self, host, port, timeout=None, **kwargs):
        self.connects.append((host, port, timeout))
        if self.fail_connect > 0:
            self.fail_connect -= 1
            raise httpcore.ConnectError("Fixture connection failure before any request bytes")
        stream = OfflineStream(self)
        self.streams.append(stream)
        return stream

    async def connect_unix_socket(self, **kwargs):
        raise AssertionError("Unexpected Unix socket")

    async def sleep(self, seconds):
        self.sleeps.append(seconds)


def provider_with_network(monkeypatch, **options):
    backend = OfflineNetwork(**options)
    # Replace only sockets. The real transport, pool, HTTP parser and retry loop run.
    monkeypatch.setattr("httpcore._async.connection_pool.AutoBackend", lambda: backend)
    settings = Settings("12345", "live_fixture_not_a_real_key", "live", True,
                        "https://billing.example.test", None, "", "", True, "npd_manual")
    return YooKassa(settings), backend


@pytest.mark.parametrize("failure", ["fail_connect", "fail_tls"])
async def test_connect_retries_recover_without_sending_duplicate_payment(monkeypatch, failure):
    provider, backend = provider_with_network(monkeypatch, **{failure: 2})
    result = await provider.request("POST", "payments", body={"amount": {"value": "390.00", "currency": "RUB"}}, key="durable-order-id")
    assert result == {"id": "offline-payment"}
    assert backend.connects == [("api.yookassa.ru", 443, 5)] * 3
    sent = [b"".join(stream.writes) for stream in backend.streams if any(stream.writes)]
    assert len(sent) == 1
    assert sent[0].count(b"POST /v3/payments HTTP/1.1") == 1
    assert b"Idempotence-Key: durable-order-id" in sent[0]
    assert b'"value":"390.00"' in sent[0]
    assert len(backend.sleeps) == 2


@pytest.mark.parametrize("failure", ["fail_connect", "fail_tls"])
async def test_connection_retry_budget_is_three_attempts_and_never_sends_payment(monkeypatch, failure):
    provider, backend = provider_with_network(monkeypatch, **{failure: 10})
    with pytest.raises(ProviderError, match="Статус платежа уточняется"):
        await provider.request("POST", "payments", body={"amount": "fixture"}, key="same-order")
    assert len(backend.connects) == 3 and len(backend.sleeps) == 2
    assert not any(any(stream.writes) for stream in backend.streams)


async def test_read_timeout_after_send_has_no_immediate_retry(monkeypatch):
    provider, backend = provider_with_network(monkeypatch, read_timeout=True)
    with pytest.raises(ProviderError, match="Повторное списание не создаётся"):
        await provider.request("POST", "payments", body={"amount": "fixture"}, key="durable-order-id")
    assert len(backend.connects) == 1 and backend.sleeps == []
    assert b"".join(backend.streams[0].writes).count(b"POST /v3/payments HTTP/1.1") == 1


async def test_http_server_error_has_no_immediate_retry(monkeypatch):
    provider, backend = provider_with_network(monkeypatch, status=500)
    with pytest.raises(ProviderError, match="Статус уточняется"):
        await provider.request("POST", "payments", body={"amount": "fixture"}, key="durable-order-id")
    assert len(backend.connects) == 1 and backend.sleeps == []


async def test_retries_keep_default_verified_tls_and_ignore_environment_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8123")
    monkeypatch.setenv("SSL_CERT_FILE", "/does-not-exist-untrusted-ca.pem")
    provider, backend = provider_with_network(monkeypatch, fail_tls=1)
    await provider.request("GET", "me")
    assert backend.connects == [("api.yookassa.ru", 443, 5)] * 2
    assert len(backend.tls) == 2
    for context, hostname, timeout in backend.tls:
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
        assert context.maximum_version == ssl.TLSVersion.MAXIMUM_SUPPORTED
        assert context.get_ca_certs()
        assert hostname == "api.yookassa.ru" and timeout == 5
