from datetime import timedelta

import pytest

from app.api import auth
from app.core import adbook, promo


class TestCodes:
    def test_generated_code_has_expected_shape(self):
        code = promo.generate_code()
        assert promo.CODE_RE.match(code), code

    def test_generated_codes_are_unique(self):
        assert len({promo.generate_code() for _ in range(500)}) > 490

    def test_ambiguous_characters_are_excluded(self):
        joined = "".join(promo.generate_code() for _ in range(200))
        for confusing in "OI01":
            assert confusing not in joined

    def test_user_typing_is_normalised(self):
        assert promo.formatted(" abcd 2345 ") == "ABCD-2345"
        assert promo.formatted("abcd-2345") == "ABCD-2345"


@pytest.mark.asyncio
async def test_redeeming_grants_plan_limits(store, owner):
    (code,) = await promo.create_codes(1, plan="pro")
    result = await promo.redeem(code, owner)

    assert result["daily_limit"] == promo.PLANS["pro"]["daily_limit"]
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id = ?", (owner,))
    assert user["max_channels"] == promo.PLANS["pro"]["max_channels"]
    assert user["access_until"] > store.utcnow()
    assert auth.has_access(user) is True


@pytest.mark.asyncio
async def test_code_cannot_be_used_by_a_second_person(store, owner):
    (code,) = await promo.create_codes(1)
    await promo.redeem(code, owner)
    await store.execute(
        "INSERT INTO users (tg_id, created_at) VALUES (2, ?)", (store.utcnow(),)
    )
    with pytest.raises(promo.PromoError, match="уже активирован"):
        await promo.redeem(code, 2)


@pytest.mark.asyncio
async def test_reentering_own_code_extends_instead_of_resetting(store, owner):
    (code,) = await promo.create_codes(1, plan="start")
    first = await promo.redeem(code, owner)
    second = await promo.redeem(code, owner)
    assert second["access_until"] > first["access_until"]


@pytest.mark.asyncio
async def test_unknown_and_malformed_codes_are_rejected(store, owner):
    with pytest.raises(promo.PromoError, match="выглядит неправильно"):
        await promo.redeem("короткий", owner)
    with pytest.raises(promo.PromoError, match="такого кода нет"):
        await promo.redeem("ZZZZ-9999", owner)


@pytest.mark.asyncio
async def test_expired_access_is_refused(store, owner):
    await store.execute(
        "UPDATE users SET access_until = ? WHERE tg_id = ?",
        (store.utcnow() - timedelta(days=1), owner),
    )
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id = ?", (owner,))
    assert auth.has_access(user) is False


@pytest.mark.asyncio
async def test_admin_never_needs_a_code(store, owner):
    await store.execute("UPDATE users SET is_admin = 1 WHERE tg_id = ?", (owner,))
    user = await store.fetch_one("SELECT * FROM users WHERE tg_id = ?", (owner,))
    assert auth.has_access(user) is True


class TestContactExtraction:
    def test_finds_telegram_username_and_link(self):
        found = adbook.extract_contacts("По вопросам рекламы @adsmanager или https://t.me/reklama_bot")
        assert "t.me/reklama_bot" in found
        assert "@adsmanager" in found

    def test_finds_email_and_phone(self):
        found = adbook.extract_contacts("Пишите ads@example.com или звоните +7 999 123-45-67")
        assert "ads@example.com" in found
        assert any("999" in c for c in found)

    def test_email_is_not_mistaken_for_a_telegram_handle(self):
        found = adbook.extract_contacts("Пишите ads@bank.ru")
        assert "@bank" not in found
        assert "ads@bank.ru" in found

    def test_advertiser_is_not_taken_from_an_email(self):
        assert adbook.extract_advertiser("На правах рекламы. Пишите ads@bank.ru") is None

    def test_company_name_becomes_advertiser(self):
        assert adbook.extract_advertiser('Реклама. ООО «Ромашка», ИНН 7701234567') == "Ромашка"

    def test_falls_back_to_mention(self):
        assert adbook.extract_advertiser("Купи курс у @guru_school") == "@guru_school"

    def test_nothing_to_find(self):
        assert adbook.extract_contacts("Просто текст без контактов") == []
        assert adbook.extract_advertiser("Просто текст") is None


AD_TEXT = (
    "Реклама. Успей забрать курс по промокоду SALE30, скидка 30%. "
    "Вопросы — @adsmanager. ООО «Ромашка», ИНН 7701234567. erid: 2VfnxwBM"
)


@pytest.mark.asyncio
async def test_rejected_ad_is_archived_with_contacts(store, channel):
    post = {
        "channel_id": channel["id"],
        "source_id": None,
        "source_title": "Донор",
        "url": "https://t.me/donor/1",
        "raw_text": AD_TEXT,
    }
    ad_id = await adbook.record(post, 13, ["промокод", "erid"])

    saved = await store.fetch_one("SELECT * FROM ad_offers WHERE id = ?", (ad_id,))
    assert saved["advertiser"] == "Ромашка"
    assert "@adsmanager" in saved["contacts"]
    assert saved["seen_count"] == 1
    assert saved["status"] == "new"


@pytest.mark.asyncio
async def test_same_advertiser_seen_again_bumps_counter(store, channel):
    post = {
        "channel_id": channel["id"],
        "source_id": None,
        "source_title": "Донор",
        "url": None,
        "raw_text": AD_TEXT,
    }
    first = await adbook.record(post, 13, ["промокод"])
    again = await adbook.record({**post, "raw_text": AD_TEXT + " Осталось мало мест!"}, 13, ["промокод"])

    assert again == first, "повтор той же рекламы не должен плодить записи"
    saved = await store.fetch_one("SELECT seen_count FROM ad_offers WHERE id = ?", (first,))
    assert saved["seen_count"] == 2
    total = await store.fetch_one("SELECT COUNT(*) AS n FROM ad_offers")
    assert total["n"] == 1


@pytest.mark.asyncio
async def test_different_advertisers_are_separate_rows(store, channel):
    base = {"channel_id": channel["id"], "source_id": None, "source_title": "Донор", "url": None}
    await adbook.record({**base, "raw_text": AD_TEXT}, 13, [])
    await adbook.record(
        {**base, "raw_text": "На правах рекламы: вклад под 18% годовых в банке, звоните @bankads"}, 5, []
    )
    total = await store.fetch_one("SELECT COUNT(*) AS n FROM ad_offers")
    assert total["n"] == 2


@pytest.mark.asyncio
async def test_empty_text_is_not_archived(store, channel):
    assert await adbook.record({"channel_id": channel["id"], "raw_text": "  "}, 5, []) is None
