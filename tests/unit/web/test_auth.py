"""Unit tests for Telegram login verification + JWT (spec section 9.2)."""

import hashlib
import hmac
import time

from planner.web.auth import create_jwt, decode_jwt, verify_telegram_login

BOT = "123456:TEST-TOKEN"


def _signed(data: dict[str, str], bot_token: str = BOT) -> dict[str, str]:
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(bot_token.encode()).digest()
    out = dict(data)
    out["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return out


def test_valid_telegram_signature_accepted():
    data = _signed({"id": "42", "first_name": "Test", "auth_date": str(int(time.time()))})
    assert verify_telegram_login(data, BOT) is True


def test_tampered_signature_rejected():
    data = _signed({"id": "42", "first_name": "Test", "auth_date": str(int(time.time()))})
    data["id"] = "99"  # mutate after signing
    assert verify_telegram_login(data, BOT) is False


def test_missing_hash_rejected():
    assert verify_telegram_login({"id": "42"}, BOT) is False


def test_stale_auth_date_rejected():
    old = str(int(time.time()) - 200000)
    data = _signed({"id": "42", "first_name": "Test", "auth_date": old})
    assert verify_telegram_login(data, BOT) is False


def test_malformed_auth_date_rejected_not_raised():
    """A signed payload with a non-int auth_date must be rejected (False),
    not raise ValueError (which would surface as a 500)."""
    data = _signed({"id": "42", "first_name": "Test", "auth_date": "not-a-number"})
    assert verify_telegram_login(data, BOT) is False


def test_missing_auth_date_rejected():
    """A signed payload without auth_date is unverifiable freshness → reject."""
    data = _signed({"id": "42", "first_name": "Test"})
    assert verify_telegram_login(data, BOT) is False


def test_jwt_round_trip():
    token = create_jwt({"sub": "u1", "is_admin": True}, "secret")
    claims = decode_jwt(token, "secret")
    assert claims is not None
    assert claims["sub"] == "u1" and claims["is_admin"] is True


def test_jwt_wrong_secret_returns_none():
    token = create_jwt({"sub": "u1"}, "secret")
    assert decode_jwt(token, "other-secret") is None
