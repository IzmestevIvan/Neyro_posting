"""Bounded, process-local HTTP telemetry. No keys, prompts or response bodies."""
import time
from collections import deque
from datetime import datetime, timezone


class ApiMetrics:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.buckets = deque(maxlen=61)
        self.active = 0
        self.waiting = 0

    def _bucket(self):
        minute = int(self.clock() // 60)
        if not self.buckets or self.buckets[-1]['minute'] != minute:
            self.buckets.append(dict(minute=minute, requests=0, errors=0, quota=0,
                                     server=0, transport=0, interrupted=0,
                                     latency_ms=0, fallbacks=0))
        return self.buckets[-1]

    def record(self, status, elapsed):
        b = self._bucket()
        b['requests'] += 1
        b['errors'] += int(status != 200)
        b['quota'] += int(status == 429)
        b['server'] += int(isinstance(status, int) and status >= 500)
        b['transport'] += int(status == 'transport')
        b['interrupted'] += int(status == 'interrupted')
        b['latency_ms'] += max(0, elapsed * 1000)

    def fallback(self):
        self._bucket()['fallbacks'] += 1

    def snapshot(self):
        minute = int(self.clock() // 60)
        windows = {}
        for size in (15, 60):
            rows = [b for b in self.buckets if minute-size < b['minute'] <= minute]
            total = {k: sum(b[k] for b in rows) for k in
                     ('requests', 'errors', 'quota', 'server', 'transport', 'interrupted', 'fallbacks', 'latency_ms')}
            total['average_ms'] = round(total.pop('latency_ms') / total['requests']) if total['requests'] else None
            windows[str(size)] = total
        by_minute = {b['minute']: b for b in self.buckets}
        series = [dict(requests=by_minute.get(m, {}).get('requests', 0),
                       errors=by_minute.get(m, {}).get('errors', 0))
                  for m in range(minute-14, minute+1)]
        return dict(started_at=self.started_at, active=self.active, waiting=self.waiting,
                    windows=windows, series=series)


metrics = ApiMetrics()
