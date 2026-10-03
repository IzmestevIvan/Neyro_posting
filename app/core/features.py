"""Reversible product switches; disabling a feature never rewrites saved data."""
import os


BUSINESS_MODE_ENABLED = os.getenv('BUSINESS_MODE_ENABLED', '').strip().lower() in {'1', 'true', 'yes', 'on'}
BUSINESS_MODE_UNAVAILABLE = 'Режим бизнеса временно недоступен. Сохранённые данные не изменены.'


def business_mode_unavailable(channel=None, post=None) -> bool:
    """Also protect company drafts retained after a channel changed modes."""
    return not BUSINESS_MODE_ENABLED and bool(
        (channel or {}).get('business_mode')
        or (post or {}).get('business_draft')
        or (post or {}).get('business_generated')
    )


def require_business_mode() -> None:
    if not BUSINESS_MODE_ENABLED:
        raise ValueError(BUSINESS_MODE_UNAVAILABLE)
