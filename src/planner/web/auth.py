"""Telegram Login Widget verification + JWT cookie (spec section 9.2).

No passwords, no registration (single-tenant): the Telegram signature proves
identity, then a short-lived JWT in an http-only cookie carries the session.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

JWT_ALGO = "HS256"
JWT_TTL_HOURS = 12
COOKIE_NAME = "planner_session"
_AUTH_MAX_AGE = 86400  # reject Telegram payloads older than a day


def verify_telegram_login(data: dict[str, str], bot_token: str) -> bool:
    """Validate the Telegram Login Widget signature (HMAC-SHA256)."""
    received_hash = data.get("hash")
    if not received_hash:
        return False

    check = "\n".join(
        f"{k}={data[k]}" for k in sorted(data) if k != "hash"
    )
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret_key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return False

    try:
        auth_date = int(data["auth_date"])
    except (KeyError, ValueError, TypeError):
        return False  # missing/malformed freshness is unverifiable → reject
    return time.time() - auth_date <= _AUTH_MAX_AGE


def create_jwt(claims: dict[str, Any], secret: str) -> str:
    payload = dict(claims)
    payload["exp"] = datetime.now(UTC) + timedelta(hours=JWT_TTL_HOURS)
    token: str = jwt.encode(payload, secret, algorithm=JWT_ALGO)
    return token


def decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
    try:
        claims: dict[str, Any] = jwt.decode(token, secret, algorithms=[JWT_ALGO])
        return claims
    except jwt.PyJWTError:
        return None
