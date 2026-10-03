"""Bounded, process-local admission before auth and request-body buffering.

Client addresses must come from the socket or Uvicorn's explicitly trusted proxy.
Never read client-supplied forwarding headers here. This is overload containment,
not a replacement for upstream DDoS filtering.
"""
from collections import OrderedDict, Counter
from ipaddress import ip_address, ip_network
from time import monotonic

from starlette.responses import JSONResponse


class TokenBuckets:
    def __init__(self, rate, burst, capacity=8192, clock=monotonic):
        self.rate, self.burst, self.capacity = rate, burst, capacity
        self.clock = clock
        self.idle_ttl = max(60, burst / rate)
        self.entries = OrderedDict()

    def admit(self, key):
        now = self.clock()
        while self.entries:
            _, (_, last) = next(iter(self.entries.items()))
            if now - last < self.idle_ttl:
                break
            self.entries.popitem(last=False)
        entry = self.entries.pop(key, None)
        if entry is None:
            # Never evict an active client's debt to make room for an attacker.
            if len(self.entries) >= self.capacity:
                return False
            tokens = self.burst
        else:
            tokens, last = entry
            tokens = min(self.burst, tokens + max(0, now - last) * self.rate)
        allowed = tokens >= 1
        self.entries[key] = (tokens - int(allowed), now)
        return allowed


def client_key(scope):
    try:
        address = ip_address(scope['client'][0])
        if address.version == 6:
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            return str(ip_network(f'{address}/64', strict=False))
        return str(address)
    except (ValueError, KeyError, TypeError, IndexError):
        return 'unknown'


class IngressMiddleware:
    def __init__(self, app, per_ip_rate=10, per_ip_burst=60,
                 global_rate=50, global_burst=100, max_inflight=32,
                 capacity=8192, clock=monotonic):
        self.app = app
        self.clients = TokenBuckets(per_ip_rate, per_ip_burst, capacity, clock)
        self.total = TokenBuckets(global_rate, global_burst, 1, clock)
        self.max_inflight = max_inflight
        self.inflight = 0
        self.rejections = Counter()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        reason, status = None, 429
        if not self.clients.admit(client_key(scope)):
            reason = 'client_limit'
        elif not self.total.admit('all'):
            reason = 'global_limit'
        elif self.inflight >= self.max_inflight:
            reason, status = 'concurrency_limit', 503
        if reason:
            self.rejections[reason] += 1
            return await JSONResponse(
                {'detail': 'Слишком много запросов. Повторите позже.'},
                status_code=status,
                headers={'Retry-After': '5', 'Cache-Control': 'no-store'},
            )(scope, receive, send)
        self.inflight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.inflight -= 1
