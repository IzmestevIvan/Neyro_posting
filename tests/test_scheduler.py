from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.scheduler import _pick_new, in_window, next_window_start

MSK = ZoneInfo("Europe/Moscow")


def channel(start: int, end: int) -> dict:
    return {"window_start": start, "window_end": end}


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 7, hour, minute, tzinfo=MSK)


def test_daytime_window():
    day = channel(8, 23)
    assert in_window(at(8), day) is True
    assert in_window(at(22), day) is True
    assert in_window(at(23), day) is False
    assert in_window(at(3), day) is False


def test_window_crossing_midnight():
    night = channel(22, 6)
    assert in_window(at(23), night) is True
    assert in_window(at(2), night) is True
    assert in_window(at(12), night) is False


def test_full_day_window_accepts_every_hour():
    full = channel(0, 24)
    assert all(in_window(at(hour), full) for hour in range(24))


def test_next_window_start_moves_to_tomorrow_when_window_passed():
    result = next_window_start(at(23, 30), channel(8, 23))
    assert (result.hour, result.minute) == (8, 0)
    assert result.day == 8


def test_next_window_start_stays_today_when_window_ahead():
    result = next_window_start(at(3), channel(8, 23))
    assert (result.day, result.hour) == (7, 8)


def test_next_window_start_survives_hour_24():
    """The panel used to allow window_start=24, which datetime.replace rejects outright."""
    result = next_window_start(at(12), channel(24, 24))
    assert 0 <= result.hour <= 23


def item(uid: str) -> SimpleNamespace:
    return SimpleNamespace(uid=uid)


def test_first_poll_takes_only_the_newest_item():
    items = [item(f"c/{i}") for i in range(10)]
    assert [i.uid for i in _pick_new(items, None)] == ["c/9"]


def test_subsequent_poll_takes_everything_after_last_seen():
    items = [item(f"c/{i}") for i in range(6)]
    assert [i.uid for i in _pick_new(items, "c/3")] == ["c/4", "c/5"]


def test_nothing_new_returns_empty():
    items = [item(f"c/{i}") for i in range(4)]
    assert _pick_new(items, "c/3") == []


def test_unknown_last_uid_falls_back_to_recent_tail():
    items = [item(f"c/{i}") for i in range(20)]
    assert len(_pick_new(items, "c/999")) == 5


def test_empty_source_is_safe():
    assert _pick_new([], None) == []
    assert _pick_new([], "c/1") == []
