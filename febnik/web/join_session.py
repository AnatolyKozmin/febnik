"""Ключи сессии и хеш одноразового кода входа по почте."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

JOIN_PENDING_EMAIL = "join_pending_email"
JOIN_OTP_HASH = "join_otp_hash"
JOIN_OTP_EXPIRES = "join_otp_expires"
JOIN_LAST_SENT = "join_last_sent"
JOIN_OTP_ATTEMPTS = "join_otp_attempts"
JOIN_VERIFIED_EMAIL = "join_verified_email"


def otp_hash(code: str, session_secret: str) -> str:
    return hmac.new(session_secret.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_otp_code() -> str:
    return f"{secrets.randbelow(900_000) + 100_000:06d}"


def clear_pending_otp(sess: dict[str, Any]) -> None:
    for k in (JOIN_PENDING_EMAIL, JOIN_OTP_HASH, JOIN_OTP_EXPIRES, JOIN_OTP_ATTEMPTS):
        sess.pop(k, None)


def now_ts() -> int:
    return int(time.time())
