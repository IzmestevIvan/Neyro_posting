"""Refuse destructive fixtures outside the dedicated local test database."""
from urllib.parse import urlsplit, unquote


def validate_test_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme not in ('postgres', 'postgresql')
            or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or unquote(parsed.path) != '/neyro_test'
            or parsed.query or parsed.fragment):
        raise ValueError('Tests require the dedicated local neyro_test database; refusing destructive fixtures')
    return url
