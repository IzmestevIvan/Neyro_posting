"""Bounded work in the single bot process. Never hold a DB connection across HTTP/AI."""
import asyncio
from collections import OrderedDict
from time import monotonic
from weakref import WeakValueDictionary

_locks = WeakValueDictionary()
AI_CONCURRENCY = 4
ai_slots = asyncio.Semaphore(AI_CONCURRENCY)
source_slots = asyncio.Semaphore(8)
delivery_slots = asyncio.Semaphore(2)
fresh_slots = asyncio.Semaphore(2)
_send_lock = asyncio.Lock()
_next_send = 0.0
_chat_send = OrderedDict()


async def wait_telegram(chat_id, messages: int = 1):
    """Stay below Telegram's aggregate rate and avoid bursts into one channel."""
    global _next_send
    while True:
        async with _send_lock:
            now = monotonic()
            delay = max(_next_send, _chat_send.get(chat_id, 0)) - now
            if delay <= 0:
                _next_send = now + max(1, messages) / 25
                _chat_send[chat_id] = now + 1.1
                _chat_send.move_to_end(chat_id)
                while len(_chat_send) > 2048:
                    _chat_send.popitem(last=False)
                return
        await asyncio.sleep(delay)


def channel_lock(channel_id: int) -> asyncio.Lock:
    lock = _locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[channel_id] = lock
    return lock


async def bounded_map(items, operation, workers: int):
    """A fixed number of workers, even when there are thousands of input rows."""
    iterator = iter(items)

    async def worker():
        for item in iterator:
            await operation(item)

    async with asyncio.TaskGroup() as group:
        for _ in range(min(workers,len(items))):
            group.create_task(worker())
