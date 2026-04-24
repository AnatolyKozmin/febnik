"""Участники, зарегистрированные только через сайт (без Telegram)."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.config import get_settings
from febnik.db.models import User, UserRole
from febnik.services.mail import normalize_email
from febnik.web.join_session import pin_hash

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


STUDENT_TICKET_DIGITS = 6


def compact_student_ticket_input(raw: str) -> str:
    """Убирает все пробельные символы (на случай вставки с пробелами)."""
    return "".join((raw or "").split())


def normalize_student_ticket(raw: str) -> str:
    """Ровно 6 цифр после удаления пробелов. Иначе ValueError."""
    compact = compact_student_ticket_input(raw)
    if not compact:
        raise ValueError("Укажите номер студенческого билета.")
    if len(compact) != STUDENT_TICKET_DIGITS or not compact.isdigit():
        raise ValueError("Номер студенческого билета — ровно 6 цифр, как на пластике.")
    return compact


def normalize_student_ticket_optional(raw: str) -> str | None:
    """Для админки: пусто → None, иначе те же правила, что у участника."""
    compact = compact_student_ticket_input(raw)
    if not compact:
        return None
    if len(compact) != STUDENT_TICKET_DIGITS or not compact.isdigit():
        raise ValueError("Номер студенческого билета — ровно 6 цифр.")
    return compact


async def get_web_user_by_student_ticket(session: AsyncSession, raw: str) -> User | None:
    compact = compact_student_ticket_input(raw)
    if len(compact) != STUDENT_TICKET_DIGITS or not compact.isdigit():
        return None
    ticket = compact
    r = await session.execute(select(User).where(User.student_ticket == ticket))
    u = r.scalar_one_or_none()
    if u and is_web_user(u):
        return u
    return None


async def get_web_user_by_pin(session: AsyncSession, plain_pin: str) -> User | None:
    """Устаревший вход по сохранённому PIN (только если в БД ещё есть web_pin_hash)."""
    raw = (plain_pin or "").strip().replace(" ", "")
    if len(raw) not in (4, 6) or not raw.isdigit():
        return None
    h = pin_hash(raw, get_settings().session_secret)
    r = await session.execute(select(User).where(User.web_pin_hash == h))
    u = r.scalar_one_or_none()
    if u and is_web_user(u):
        return u
    return None


async def create_web_participant(session: AsyncSession, full_name: str, student_ticket: str) -> User:
    """Создаёт веб-участника; вход позже — по номеру студенческого билета."""
    raw = full_name.strip()
    if not raw:
        raise ValueError("Укажите фамилию и имя.")
    ticket = normalize_student_ticket(student_ticket)
    dup = await session.execute(select(User.id).where(User.student_ticket == ticket))
    if dup.scalar_one_or_none() is not None:
        raise ValueError("Этот номер студенческого билета уже зарегистрирован. Если это вы — войдите через «Уже регистрировался».")
    temp_tid = -secrets.randbelow(2**62)
    u = User(
        telegram_id=temp_tid,
        username=None,
        email=None,
        student_ticket=ticket,
        web_pin_hash=None,
        full_name=raw[:512],
        role=UserRole.participant,
        balance_feb=0,
    )
    session.add(u)
    await session.flush()
    u.telegram_id = web_synthetic_telegram_id(u.id)
    await session.flush()
    return u
