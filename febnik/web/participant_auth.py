"""Вход участника после подтверждения почты: отдельная подписанная cookie (не ключ в общей server-side сессии).

Шаг /join (код в письме) по-прежнему хранит OTP во временных ключах Starlette Session — это только на минуты до ввода кода."""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from febnik.config import get_settings

PARTICIPANT_COOKIE = "febnik_p"
LEGACY_SESSION_PARTICIPANT_KEY = "participant_user_id"
_TOKEN_SALT = "febnik-participant-v1"


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_TOKEN_SALT)


def _issue_token(user_id: int, secret: str) -> str:
    return _serializer(secret).dumps({"u": int(user_id)})


def _parse_token(token: str, secret: str, max_age: int) -> int | None:
    try:
        data = _serializer(secret).loads(token, max_age=max_age)
        uid = int(data["u"])
        return uid if uid > 0 else None
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def get_participant_user_id(request: Request) -> int | None:
    """ID участника: сначала подписанная cookie, иначе устаревший ключ в session (миграция)."""
    settings = get_settings()
    raw = request.cookies.get(PARTICIPANT_COOKIE)
    if raw:
        uid = _parse_token(raw, settings.session_secret, settings.participant_token_max_age_seconds)
        if uid is not None:
            return uid
    legacy = request.session.get(LEGACY_SESSION_PARTICIPANT_KEY)
    return int(legacy) if legacy is not None else None


def attach_participant(response: Response, request: Request, user_id: int) -> None:
    settings = get_settings()
    token = _issue_token(user_id, settings.session_secret)
    response.set_cookie(
        key=PARTICIPANT_COOKIE,
        value=token,
        max_age=settings.participant_token_max_age_seconds,
        httponly=True,
        samesite="lax",
        path="/",
        secure=settings.web_cookie_secure,
    )
    request.session.pop(LEGACY_SESSION_PARTICIPANT_KEY, None)


def clear_participant(response: Response, request: Request) -> None:
    response.delete_cookie(PARTICIPANT_COOKIE, path="/")
    request.session.pop(LEGACY_SESSION_PARTICIPANT_KEY, None)
