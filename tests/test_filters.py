from app.core.filters import (
    AD_THRESHOLD,
    ad_score,
    find_duplicate,
    fingerprint,
    stopword_hit,
    strip_source_artifacts,
    tokens,
)

REAL_ADS = [
    "Успей забрать курс по промокоду SALE30, скидка 30%. erid: 2VfnxwBM",
    "На правах рекламы. Открой вклад под 18% годовых.",
    "Партнёрский материал: как выбрать ноутбук в 2026 году.",
    "Регистрируйся и получи бонус 5000 рублей в казино — жми сюда!",
    "Реклама. ООО «Ромашка», ИНН 7701234567. Скидка 40% по промокоду LETO.",
]

REAL_NEWS = [
    "Реклама на телеканалах подорожает на 15% в 2027 году, сообщили в АКАР.",
    "Мэрия отчиталась о ремонте дорог: скидка 20% на проезд для студентов сохранится.",
    "В Москве открылся новый парк, вход свободный.",
    "Суд оштрафовал букмекера за нарушение закона о рекламе.",
    "Собянин открыл новую станцию метро на юге Москвы.",
    "Учёные придумали гуманную ловушку для комаров — она засасывает насекомых.",
]


def test_real_ads_are_blocked():
    for text in REAL_ADS:
        score, reasons = ad_score(text)
        assert score >= AD_THRESHOLD, f"пропустили рекламу: {text!r} (счёт {score}, {reasons})"


def test_news_about_advertising_survives():
    for text in REAL_NEWS:
        score, reasons = ad_score(text)
        assert score < AD_THRESHOLD, f"новость принята за рекламу: {text!r} (счёт {score}, {reasons})"


DUPLICATES = [
    (
        "Бабье лето ненадолго придёт в Москву на этой неделе — тёплая и сухая погода "
        "продержится со вторника по четверг. Самым тёплым днём станет четверг, +25.",
        "В Москве на этой неделе ждут бабье лето: со вторника по четверг тепло и сухо. "
        "Четверг станет самым тёплым днём, воздух прогреется до +25 градусов.",
    ),
    (
        "МЧС объявило штормовое предупреждение в Подмосковье из-за ветра до 20 м/с в ночь на среду.",
        "В Подмосковье объявлено штормовое предупреждение: ночью в среду ожидается "
        "усиление ветра до 20 метров в секунду, сообщили в МЧС.",
    ),
]

DISTINCT = [
    (
        "Бабье лето придёт в Москву на этой неделе, тёплая и сухая погода.",
        "Учёные придумали гуманную ловушку для комаров — она засасывает насекомых.",
    ),
    (
        "В Москве отремонтируют участок Ленинского проспекта этим летом.",
        "В Москве закроют на ремонт участок Кутузовского проспекта следующей весной.",
    ),
    (
        "Собянин открыл новую станцию метро на юге Москвы.",
        "Собянин рассказал о планах реновации жилья в Москве до 2030 года.",
    ),
]


def test_reworded_duplicates_are_caught():
    for first, second in DUPLICATES:
        assert find_duplicate(fingerprint(second), [(1, fingerprint(first))]) == 1


def test_different_news_are_not_duplicates():
    for first, second in DISTINCT:
        assert find_duplicate(fingerprint(second), [(1, fingerprint(first))]) is None


def test_short_texts_never_match():
    assert find_duplicate(fingerprint("Москва"), [(1, fingerprint("Москва"))]) is None


def test_stopwords_are_case_insensitive():
    assert stopword_hit("Лучшее КАЗИНО города", "казино, промокод") == "казино"
    assert stopword_hit("Обычная новость", "казино, промокод") is None
    assert stopword_hit("Что угодно", "") is None


def test_source_artifacts_are_stripped():
    cleaned = strip_source_artifacts(
        "Важная новость дня.\n\nПодписывайтесь на наш канал @moscowach https://t.me/moscowach"
    )
    assert "moscowach" not in cleaned
    assert "Подписывайтесь" not in cleaned
    assert cleaned.startswith("Важная новость")


def test_blank_runs_are_collapsed():
    assert strip_source_artifacts("Абзац\n\n\n\n\nВторой абзац") == "Абзац\n\nВторой абзац"


def test_tokens_ignore_noise_words():
    assert "в" not in tokens("в Москве")
    assert tokens("") == set()
