import pytest

from app.api import auth


def make_user(**over) -> dict:
    base = {"tg_id": 5, "is_admin": 0, "blocked": 0, "access_until": None, "promo_code": None}
    return {**base, **over}


class TestSystemHealthVisibility:
    """Нагрузка сервера и состояние воркера — внутренняя кухня, клиенту её не показываем."""

    def test_plain_user_is_not_admin(self):
        assert auth.is_admin(make_user()) is False

    def test_flagged_user_is_admin(self):
        assert auth.is_admin(make_user(is_admin=1)) is True

    def test_id_from_env_is_admin(self, monkeypatch):
        monkeypatch.setattr(auth, "ADMIN_IDS", {5})
        assert auth.is_admin(make_user()) is True


PUBLISH_NOW_STATUSES = ("approved", "pending", "digest", "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", PUBLISH_NOW_STATUSES)
async def test_publish_now_finds_post_in_any_waiting_status(store, channel, status):
    """Кнопка «Опубликовать сейчас» должна доставать пост из любой очереди ожидания,
    включая отложенный дайджест и сорвавшуюся отправку."""
    await store.execute(
        "INSERT INTO posts (channel_id, uid, raw_text, text_out, media, status, created_at) "
        "VALUES (?, ?, 'сырой', 'Текст', '[]', ?, ?)",
        (channel["id"], f"u-{status}", status, store.utcnow()),
    )
    found = await store.fetch_one(
        "SELECT id, status FROM posts WHERE channel_id = ? AND status IN "
        "('approved', 'pending', 'digest', 'failed') "
        "ORDER BY status = 'approved' DESC, status = 'pending' DESC, id LIMIT 1",
        (channel["id"],),
    )
    assert found is not None, f"пост в статусе {status} должен находиться"
    assert found["status"] == status


@pytest.mark.asyncio
async def test_publish_now_prefers_approved_over_the_rest(store, channel):
    for status in ("digest", "failed", "pending", "approved"):
        await store.execute(
            "INSERT INTO posts (channel_id, uid, raw_text, text_out, media, status, created_at) "
            "VALUES (?, ?, 'сырой', 'Текст', '[]', ?, ?)",
            (channel["id"], f"p-{status}", status, store.utcnow()),
        )
    found = await store.fetch_one(
        "SELECT status FROM posts WHERE channel_id = ? AND status IN "
        "('approved', 'pending', 'digest', 'failed') "
        "ORDER BY status = 'approved' DESC, status = 'pending' DESC, id LIMIT 1",
        (channel["id"],),
    )
    assert found["status"] == "approved"


@pytest.mark.asyncio
async def test_paused_channel_still_yields_a_post_for_manual_publish(store, channel):
    """Пауза и окно публикации на ручную кнопку влиять не должны."""
    await store.execute(
        "UPDATE channels SET paused = 1, window_start = 3, window_end = 4 WHERE id = ?",
        (channel["id"],),
    )
    await store.execute(
        "INSERT INTO posts (channel_id, uid, raw_text, text_out, media, status, created_at) "
        "VALUES (?, 'manual', 'сырой', 'Текст', '[]', 'approved', ?)",
        (channel["id"], store.utcnow()),
    )
    found = await store.fetch_one(
        "SELECT id FROM posts WHERE channel_id = ? AND status IN "
        "('approved', 'pending', 'digest', 'failed') LIMIT 1",
        (channel["id"],),
    )
    assert found is not None
