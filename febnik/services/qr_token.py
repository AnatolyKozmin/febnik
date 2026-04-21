"""Подписанные токены для QR (идентификация участника по ссылке)."""

from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer

from febnik.config import get_settings


def _serializer() -> URLSafeSerializer:
    s = get_settings()
    return URLSafeSerializer(s.session_secret, salt="febnik-qr-v1")


def make_participant_scan_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def parse_participant_scan_token(token: str) -> int | None:
    try:
        data = _serializer().loads(token, max_age=365 * 24 * 3600)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    if not isinstance(uid, int):
        return None
    return uid
