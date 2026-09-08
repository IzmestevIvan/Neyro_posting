from app.sources.telegram_web import (
    median_views,
    normalize_ref,
    parse_channel_page,
    parse_post_link,
    parse_views,
)

PAGE = """
<div class="tgme_channel_info_header_title"><span>Москвач • Новости Москвы</span></div>
<div class="tgme_widget_message" data-post="moscowach/100">
  <div class="tgme_widget_message_text">Первая строка<br>Вторая строка</div>
  <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn/a.jpg')"></a>
  <span class="tgme_widget_message_views">12.5K</span>
  <span class="tgme_widget_message_meta"><time class="time" datetime="2026-09-07T10:00:00+00:00"></time></span>
</div>
<div class="tgme_widget_message" data-post="moscowach/101">
  <div class="tgme_widget_message_forwarded_from">переслано</div>
  <div class="tgme_widget_message_text">Репост, который брать не надо</div>
</div>
<div class="tgme_widget_message" data-post="moscowach/102">
  <div class="tgme_widget_message_text">Пост с видео</div>
  <video class="tgme_widget_message_video" src="https://cdn/v.mp4"></video>
  <span class="tgme_widget_message_views">900</span>
</div>
"""


class TestViews:
    def test_plain_number(self):
        assert parse_views("573") == 573

    def test_thousands_and_millions(self):
        assert parse_views("12.5K") == 12500
        assert parse_views("2M") == 2_000_000

    def test_comma_decimal_separator(self):
        assert parse_views("1,2K") == 1200

    def test_garbage_is_zero(self):
        assert parse_views("") == 0
        assert parse_views("много") == 0


class TestNormalizeRef:
    def test_at_prefix(self):
        assert normalize_ref("@moscowach") == "moscowach"

    def test_full_link(self):
        assert normalize_ref("https://t.me/moscowach") == "moscowach"

    def test_preview_link(self):
        assert normalize_ref("https://t.me/s/moscowach") == "moscowach"

    def test_link_without_protocol(self):
        """Users paste t.me/name constantly; it used to become the source "t.me"."""
        assert normalize_ref("t.me/moscowach") == "moscowach"

    def test_link_to_specific_post(self):
        assert normalize_ref("https://t.me/moscowach/42987") == "moscowach"

    def test_query_string_is_dropped(self):
        assert normalize_ref("https://t.me/moscowach?before=100") == "moscowach"

    def test_surrounding_whitespace(self):
        assert normalize_ref("  @moscowach  ") == "moscowach"


class TestPostLink:
    def test_full_link(self):
        assert parse_post_link("https://t.me/moscowach/42987") == ("moscowach", 42987)

    def test_preview_link(self):
        assert parse_post_link("https://t.me/s/moscowach/42987") == ("moscowach", 42987)

    def test_without_protocol(self):
        assert parse_post_link("t.me/moscowach/42987") == ("moscowach", 42987)

    def test_channel_link_is_not_a_post(self):
        assert parse_post_link("https://t.me/moscowach") is None

    def test_foreign_url_is_not_a_post(self):
        assert parse_post_link("https://example.com/a/1") is None


class TestPageParsing:
    def test_extracts_posts_and_skips_reposts(self):
        items, title = parse_channel_page(PAGE)
        assert title == "Москвач • Новости Москвы"
        assert [i.uid for i in items] == ["moscowach/100", "moscowach/102"]

    def test_line_breaks_become_newlines(self):
        items, _ = parse_channel_page(PAGE)
        assert items[0].text == "Первая строка\nВторая строка"

    def test_photo_and_video_are_detected(self):
        items, _ = parse_channel_page(PAGE)
        assert items[0].media == [{"type": "photo", "url": "https://cdn/a.jpg"}]
        assert items[1].media == [{"type": "video", "url": "https://cdn/v.mp4"}]

    def test_views_and_date_are_parsed(self):
        items, _ = parse_channel_page(PAGE)
        assert items[0].views == 12500
        assert items[0].date == "2026-09-07T10:00:00+00:00"

    def test_empty_page_is_safe(self):
        assert parse_channel_page("<html></html>") == ([], None)

    def test_median_ignores_zero_views(self):
        items, _ = parse_channel_page(PAGE)
        assert median_views(items) == 6700
        assert median_views([]) == 0
