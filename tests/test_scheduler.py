from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.scheduler import _chronological, _pick_new, in_window, next_window_start

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


def test_popularity_never_compares_new_post_to_old_hits():
    from datetime import timedelta
    from app.core.scheduler import popularity_threshold
    now = at(12)
    fresh = SimpleNamespace(uid='new', views=1000, date=(now-timedelta(minutes=2)).isoformat())
    old = [SimpleNamespace(uid=str(i), views=150000, date=(now-timedelta(hours=i+1)).isoformat()) for i in range(5)]
    assert popularity_threshold(fresh, old+[fresh], now) is None


def test_popularity_requires_three_similarly_aged_peers():
    from datetime import timedelta
    from app.core.scheduler import popularity_threshold
    now = at(12)
    fresh = SimpleNamespace(uid='new', views=10, date=(now-timedelta(minutes=10)).isoformat())
    peers = [SimpleNamespace(uid=str(i), views=v, date=(now-timedelta(minutes=8+i)).isoformat()) for i,v in enumerate([100,200,300])]
    assert popularity_threshold(fresh, peers+[fresh], now) == 200
    assert popularity_threshold(fresh, peers[:2]+[fresh], now) is None
    fresh.date = None
    assert popularity_threshold(fresh, peers, now) is None


def test_source_items_are_normalized_to_chronological_order():
    first = SimpleNamespace(uid="first", date="2026-09-07T10:00:00+00:00")
    last = SimpleNamespace(uid="last", date="2026-09-07T12:00:00+00:00")
    unknown = SimpleNamespace(uid="unknown", date=None)
    assert [item.uid for item in _chronological([last, unknown, first])] == [unknown.uid, first.uid, last.uid]
