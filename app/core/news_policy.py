"""Fresh-news policy; explicit manual drafts and digests keep their own lifecycle."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime

FRESH_FOR = timedelta(hours=1)


def realtime(channel):
    return channel.get('delay_mode') == 'instant' and not channel.get('digest_enabled')


def window_open(channel, instant=None):
    instant = instant or datetime.now(timezone.utc)
    try:
        local = instant.astimezone(ZoneInfo(channel.get('tz') or 'Europe/Moscow'))
    except Exception:
        local = instant.astimezone(ZoneInfo('Europe/Moscow'))
    start, end = channel['window_start'], channel['window_end']
    return start <= local.hour < end if start < end else local.hour >= start or local.hour < end


def source_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def stale(post, channel, instant=None):
    retention = FRESH_FOR if realtime(channel) else timedelta(hours=24)
    return (not post.get('is_manual') and
            post['created_at'] < (instant or datetime.now(timezone.utc)) - retention)
