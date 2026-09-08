import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from app.api import auth
from app.api.routes import _clean_settings

TOKEN = "123456:TEST-TOKEN"


def sign(fields: dict, token: str = TOKEN) -> str:
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(auth, "BOT_TOKEN", TOKEN)


def valid_fields(**overrides) -> dict:
    return {
        "auth_date": str(int(time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": 42, "first_name": "Иван"}, ensure_ascii=False),
        **overrides,
    }


def test_valid_signature_returns_user():
    user = auth.verify_init_data(sign(valid_fields()))
    assert user["id"] == 42


def test_tampered_payload_is_rejected():
    raw = sign(valid_fields())
    tampered = raw.replace("%2242%22", "%2299%22").replace("42", "99", 1)
    with pytest.raises(HTTPException) as err:
        auth.verify_init_data(tampered)
    assert err.value.status_code == 401


def test_signature_from_another_token_is_rejected():
    with pytest.raises(HTTPException) as err:
        auth.verify_init_data(sign(valid_fields(), token="999:OTHER"))
    assert err.value.status_code == 401


def test_missing_hash_is_rejected():
    with pytest.raises(HTTPException):
        auth.verify_init_data(urlencode(valid_fields()))


def test_expired_session_is_rejected():
    old = str(int(time.time()) - auth.MAX_AGE - 60)
    with pytest.raises(HTTPException) as err:
        auth.verify_init_data(sign(valid_fields(auth_date=old)))
    assert "устарела" in err.value.detail


def test_broken_auth_date_gives_401_not_500():
    with pytest.raises(HTTPException) as err:
        auth.verify_init_data(sign(valid_fields(auth_date="не-число")))
    assert err.value.status_code == 401


def test_empty_init_data_is_rejected():
    with pytest.raises(HTTPException):
        auth.verify_init_data("")


class TestSettingsWhitelist:
    def test_unknown_keys_are_dropped(self):
        with pytest.raises(HTTPException):
            _clean_settings({"owner_id": 1, "id": 5, "chat_id": -100})

    def test_sql_shaped_key_is_ignored(self):
        with pytest.raises(HTTPException):
            _clean_settings({"autopost = 1, owner_id": 7})

    def test_booleans_are_normalised(self):
        assert _clean_settings({"autopost": "да"}) == {"autopost": 1}
        assert _clean_settings({"autopost": ""}) == {"autopost": 0}

    def test_enums_reject_unknown_values(self):
        with pytest.raises(HTTPException):
            _clean_settings({"quality": "суперсуперпостинг"})
        with pytest.raises(HTTPException):
            _clean_settings({"tz": "Mars/Olympus"})
        assert _clean_settings({"quality": "fast"}) == {"quality": "fast"}

    def test_window_hours_are_clamped_to_valid_datetime_range(self):
        assert _clean_settings({"window_start": 99})["window_start"] == 23
        assert _clean_settings({"window_start": -5})["window_start"] == 0
        assert _clean_settings({"window_end": 99})["window_end"] == 24
        assert _clean_settings({"window_end": 0})["window_end"] == 1

    def test_digest_time_format_is_enforced(self):
        assert _clean_settings({"digest_time": "21:30"}) == {"digest_time": "21:30"}
        with pytest.raises(HTTPException):
            _clean_settings({"digest_time": "вечером"})

    def test_free_text_is_length_capped(self):
        assert len(_clean_settings({"instructions": "я" * 9000})["instructions"]) == 4000
