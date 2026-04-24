"""Лимиты повторного ввода при входе (студбилет / устаревший PIN)."""

from __future__ import annotations

import hashlib
import hmac
import time

RETURN_ATTEMPTS = "join_return_attempts"


def pin_hash(code: str, session_secret: str) -> str:
    return hmac.new(session_secret.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()


def now_ts() -> int:
    return int(time.time())
