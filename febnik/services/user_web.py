"""Участники, зарегистрированные только через сайт (без Telegram)."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.db.models import User, UserRole
from febnik.services.mail import normalize_email

# Отрицательные telegram_id: не пересекаются с реальными ID в Telegram.
_WEB_TG_BASE = 10**15


def is_web_user(user: User) -> bool:
    return user.telegram_id < 0


def web_synthetic_telegram_id(internal_user_id: int) -> int:
    return -_WEB_TG_BASE - int(internal_user_id)


async def get_web_user_by_email(session: AsyncSession, email: str) -> User | None:
    em = normalize_email(email)
    if not em:
        return None
    r = await session.execute(select(User).where(User.email == em))
    return r.scalar_one_or_none()


async def create_web_participant(session: AsyncSession, full_name: str, email: str) -> User:
    """Создаёт участника с подтверждённой почтой; до flush — временный отрицательный telegram_id."""
    raw = full_name.strip()
    if not raw:
        raise ValueError("Укажите ФИО.")
    em = normalize_email(email)
    if not em:
        raise ValueError("Укажите корректный адрес почты.")
    existing = await get_web_user_by_email(session, em)
    if existing:
        raise ValueError("Этот адрес уже зарегистрирован. Войдите через «Код из письма».")
    temp_tid = -secrets.randbelow(2**62)
    u = User(
        telegram_id=temp_tid,
        username=None,
        email=em,
        full_name=raw[:512],
        role=UserRole.participant,
        balance_feb=0,
    )
    session.add(u)
    await session.flush()
    u.telegram_id = web_synthetic_telegram_id(u.id)
    await session.flush()
    return u
