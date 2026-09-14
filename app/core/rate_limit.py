"""Small, bounded admission counters for authenticated API and bot requests."""
from collections import OrderedDict
from time import monotonic

_buckets = OrderedDict()


def admit(key, limit: int, period: float = 60) -> bool:
    now = monotonic()
    start, count = _buckets.pop(key, (now, 0))
    if now - start >= period:
        start, count = now, 0
    allowed = count < limit
    _buckets[key] = (start, count + int(allowed))
    while len(_buckets) > 4096:
        _buckets.popitem(last=False)
    return allowed
