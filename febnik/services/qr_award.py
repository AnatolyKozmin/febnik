"""Начисление ФЭБарт по QR для организатора (скан): общая логика формы и JSON API."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from febnik.config import get_settings
from febnik.db.models import Transaction, User
from febnik.services.balance import apply_participant_scan_reward_idempotent
from febnik.services.qr_token import parse_participant_scan_token
from febnik.services.user_web import is_web_user


@dataclass(frozen=True)
class QrAwardOk:
    user: User
    tx: Transaction
    replay: bool


async def admin_try_award_from_qr(
    session: AsyncSession,
    *,
    token: str,
    award_amount: int,
    idempotency_key: str = "",
) -> QrAwardOk | str:
    """
    Пытается начислить баллы по токену из QR.
    Успех — QrAwardOk; иначе текст ошибки для пользователя.
    """
    settings = get_settings()
    uid = parse_participant_scan_token(token.strip())
    if uid is None:
        return "Недействительная ссылка начисления."
    user = await session.get(User, uid)
    if not user or not is_web_user(user):
        return "Участник не найден или не веб-профиль."
    if award_amount < 1:
        return "Сумма начисления должна быть не меньше 1 ФЭБарт."
    if award_amount > settings.max_qr_award_feb:
        return f"Слишком много: максимум {settings.max_qr_award_feb} ФЭБарт за одно начисление."
    try:
        tx, replay = await apply_participant_scan_reward_idempotent(
            session,
            user,
            award_amount,
            note=f"Скан QR, начислено {award_amount} ФЭБарт",
            idempotency_key=idempotency_key or None,
        )
    except ValueError as e:
        return str(e)
    await session.flush()
    return QrAwardOk(user=user, tx=tx, replay=replay)
