import pytest

from app.core import publisher, scheduler


class FakeMessage:
    def __init__(self, message_id: int = 555):
        self.message_id = message_id


class FakeBot:
    """Records what would be sent to Telegram instead of sending it."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise RuntimeError("Telegram недоступен")
        self.sent.append({"chat": chat_id, "text": text, **kw})
        return FakeMessage(len(self.sent))

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        if self.fail:
            raise RuntimeError("Telegram недоступен")
        self.sent.append({"chat": chat_id, "photo": photo, "caption": caption})
        return FakeMessage(len(self.sent))

    send_video = send_photo

    async def send_media_group(self, chat_id, group, **kw):
        self.sent.append({"chat": chat_id, "group": len(group)})
        return [FakeMessage(len(self.sent))]


async def make_post(db, channel, status="approved", text="Готовый текст поста", uid=None):
    post_id = await db.insert(
        "INSERT INTO posts (channel_id, uid, raw_text, text_out, media, status, created_at) "
        "VALUES (?, ?, 'сырой', ?, '[]', ?, ?)",
        (channel["id"], uid or f"u-{status}-{text[:8]}", text, status, db.utcnow()),
    )
    return await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post_id,))


@pytest.mark.asyncio
async def test_publish_marks_post_and_counts_the_day(store, channel):
    bot = FakeBot()
    post = await make_post(store, channel)
    await publisher.publish_post(bot, post, channel)

    fresh = await store.fetch_one("SELECT * FROM posts WHERE id = ?", (post["id"],))
    assert fresh["status"] == "published"
    assert fresh["published_at"] is not None
    stat = await store.fetch_one("SELECT published FROM stats_daily WHERE channel_id = ?", (channel["id"],))
    assert stat["published"] == 1
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_second_publish_of_same_post_is_refused(store, channel):
    bot = FakeBot()
    post = await make_post(store, channel)
    await publisher.publish_post(bot, post, channel)

    with pytest.raises(publisher.AlreadyPublished):
        await publisher.publish_post(bot, post, channel)
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_failed_send_keeps_post_queued_and_counts_attempt(store, channel):
    bot = FakeBot(fail=True)
    post = await make_post(store, channel)

    with pytest.raises(RuntimeError):
        await publisher.publish_post(bot, post, channel)

    fresh = await store.fetch_one("SELECT * FROM posts WHERE id = ?", (post["id"],))
    assert fresh["status"] == "approved", "пост должен остаться в очереди для повтора"
    assert fresh["attempts"] == 1
    assert "Telegram" in fresh["reason"]


@pytest.mark.asyncio
async def test_post_gives_up_after_max_attempts(store, channel):
    bot = FakeBot(fail=True)
    post = await make_post(store, channel)
    for _ in range(publisher.MAX_ATTEMPTS):
        post = await store.fetch_one("SELECT * FROM posts WHERE id = ?", (post["id"],))
        with pytest.raises(RuntimeError):
            await publisher.publish_post(bot, post, channel)

    fresh = await store.fetch_one("SELECT * FROM posts WHERE id = ?", (post["id"],))
    assert fresh["status"] == "failed"


@pytest.mark.asyncio
async def test_daily_quota_counts_down_and_stops(store, channel, owner):
    bot = FakeBot()
    assert await publisher.quota_left(owner) == 3
    for i in range(3):
        await publisher.publish_post(bot, await make_post(store, channel, text=f"пост {i}"), channel)
    assert await publisher.quota_left(owner) == 0


@pytest.mark.asyncio
async def test_quota_exhaustion_keeps_posts_instead_of_failing_them(store, channel):
    bot = FakeBot()
    for i in range(3):
        await publisher.publish_post(bot, await make_post(store, channel, text=f"расход {i}"), channel)
    leftover = await make_post(store, channel, text="этот пост должен дождаться завтра")

    await scheduler.publish_due(bot)

    fresh = await store.fetch_one("SELECT * FROM posts WHERE id = ?", (leftover["id"],))
    assert fresh["status"] == "approved"


@pytest.mark.asyncio
async def test_pruning_removes_old_noise_but_keeps_counters(store, channel):
    from datetime import timedelta

    old = store.utcnow() - timedelta(days=30)
    await store.execute(
        "INSERT INTO posts (channel_id, uid, raw_text, media, status, created_at) "
        "VALUES (?, 'old', 'x', '[]', 'filtered', ?)",
        (channel["id"], old),
    )
    await store.execute(
        "INSERT INTO posts (channel_id, uid, raw_text, media, status, created_at) "
        "VALUES (?, 'recent', 'x', '[]', 'filtered', ?)",
        (channel["id"], store.utcnow()),
    )
    await store.bump_stat(channel["id"], store.utcnow().date(), "filtered", 2)

    await scheduler.prune_posts()

    left = await store.fetch_all("SELECT uid FROM posts WHERE channel_id = ?", (channel["id"],))
    assert [r["uid"] for r in left] == ["recent"]
    stat = await store.fetch_one("SELECT filtered FROM stats_daily WHERE channel_id = ?", (channel["id"],))
    assert stat["filtered"] == 2, "исторические счётчики должны пережить очистку"


@pytest.mark.asyncio
async def test_long_post_is_sent_within_telegram_limit(store, channel):
    bot = FakeBot()
    post = await make_post(store, channel, text="Я" * 6000)
    await publisher.publish_post(bot, post, channel)
    assert len(bot.sent[0]["text"]) <= publisher.TEXT_LIMIT
