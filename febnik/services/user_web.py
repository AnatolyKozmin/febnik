"""Участники, зарегистрированные только через сайт (без Telegram)."""

import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from febnik.db.models import User, UserRole

# Отрицательные telegram_id: не пересекаются с реальными ID в Telegram.
_WEB_TG_BASE = 10**15


def is_web_user(user: User) -> bool:
    return user.telegram_id < 0


def web_synthetic_telegram_id(internal_user_id: int) -> int:
    return -_WEB_TG_BASE - int(internal_user_id)


async def create_web_participant(session: AsyncSession, full_name: str) -> User:
    """Создаёт участника; до flush используется временный уникальный отрицательный telegram_id."""
    raw = full_name.strip()
    if not raw:
        raise ValueError("Укажите ФИО.")
    temp_tid = -secrets.randbelow(2**62)
    u = User(
        telegram_id=temp_tid,
        username=None,
        full_name=raw[:512],
        role=UserRole.participant,
        balance_feb=0,
    )
    session.add(u)
    await session.flush()
    u.telegram_id = web_synthetic_telegram_id(u.id)
    await session.flush()
    return u
