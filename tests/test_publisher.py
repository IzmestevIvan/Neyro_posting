import html

from app.core.publisher import (
    CAPTION_LIMIT,
    TEXT_LIMIT,
    _fit_escaped,
    fits_caption,
    render,
    signature_html,
)

PLAIN = {"signature_text": "", "signature_url": "", "username": "mychan"}
SIGNED = {"signature_text": "Подписаться", "signature_url": "", "username": "mychan"}


def test_signature_links_to_own_channel_when_url_empty():
    assert signature_html(SIGNED) == '<a href="https://t.me/mychan">Подписаться</a>'


def test_signature_is_empty_without_text():
    assert signature_html(PLAIN) == ""


def test_signature_escapes_hostile_text():
    hostile = {"signature_text": '<script>x</script>', "signature_url": 'a" onload="x', "username": ""}
    rendered = signature_html(hostile)
    assert "<script>" not in rendered
    assert 'onload="x' not in rendered


def test_special_characters_are_escaped():
    out = render(PLAIN, "Ставка < 5% & выше > нуля")
    assert "&lt;" in out and "&amp;" in out and "&gt;" in out


def test_long_text_never_exceeds_limit_and_keeps_entities_intact():
    # The tail is the dangerous part: naive slicing used to cut "&lt;" in half.
    text = "А" * 4090 + " <b>тег и & амперсанд в самом конце"
    out = render(SIGNED, text)
    assert len(out) <= TEXT_LIMIT
    assert html.unescape(out) is not None
    assert "&l" not in out.replace("&lt;", "").replace("&amp;", "").replace("&gt;", "")
    assert out.count("&") == out.count("&lt;") + out.count("&amp;") + out.count("&gt;")


def test_truncated_text_still_carries_the_signature():
    out = render(SIGNED, "Б" * 5000)
    assert out.endswith("</a>")
    assert "Подписаться" in out
    assert len(out) <= TEXT_LIMIT


def test_caption_limit_is_respected():
    out = render(SIGNED, "В" * 3000, CAPTION_LIMIT)
    assert len(out) <= CAPTION_LIMIT


def test_fits_caption_matches_rendered_length():
    short, long = "Коротко", "Г" * 2000
    assert fits_caption(SIGNED, short) is True
    assert fits_caption(SIGNED, long) is False
    assert len(render(SIGNED, short, CAPTION_LIMIT)) <= CAPTION_LIMIT


def test_fit_escaped_leaves_short_text_untouched():
    assert _fit_escaped("привет", 100) == "привет"


def test_fit_escaped_marks_truncation():
    out = _fit_escaped("слово " * 200, 60)
    assert out.endswith("…")
    assert len(out) <= 60


def test_fit_escaped_handles_all_entity_text():
    # Every character expands to 5 chars when escaped; the budget must still hold.
    out = _fit_escaped("<" * 300, 50)
    assert len(out) <= 50
    assert "&l" not in out.replace("&lt;", "")
