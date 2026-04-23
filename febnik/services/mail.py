"""Отправка писем (код входа участника). При пустом SMTP_HOST код только пишется в лог — для локальной разработки."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from febnik.config import Settings

logger = logging.getLogger(__name__)


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def looks_like_email(s: str) -> bool:
    if not s or len(s) > 254:
        return False
    if " " in s or s.count("@") != 1:
        return False
    local, _, domain = s.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return True


def _smtp_from_header(settings: Settings) -> str:
    """Gmail и др.: часто достаточно SMTP_USER; отдельный SMTP_FROM опционален."""
    f = (settings.smtp_from or settings.smtp_user or "").strip()
    if not f:
        raise ValueError("Задайте SMTP_FROM или SMTP_USER для отправки почты.")
    return f


def _smtp_password_clean(settings: Settings) -> str:
    """Пароль приложения Gmail часто копируют с пробелами — убираем пробелы и переводы строк."""
    return (settings.smtp_password or "").replace(" ", "").replace("\n", "").replace("\r", "").strip()


def _send_otp_sync(
    settings: Settings,
    to_email: str,
    code: str,
    ttl_minutes: int,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Код входа — ФЭБник"
    msg["From"] = _smtp_from_header(settings)
    msg["To"] = to_email
    msg.set_content(
        f"Ваш код для входа в кабинет ФЭБник: {code}\n\n"
        f"Код действителен {ttl_minutes} мин. Если вы не запрашивали вход, просто проигнорируйте письмо.\n"
    )
    pwd = _smtp_password_clean(settings)
    user = (settings.smtp_user or "").strip()
    logger.info(
        "SMTP: подключение %s:%s STARTTLS=%s login=%s",
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_starttls,
        bool(user),
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if user:
                smtp.login(user, pwd)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        logger.exception("SMTP: ошибка авторизации (проверьте пароль приложения и SMTP_USER)")
        raise
    except OSError as e:
        logger.exception("SMTP: сеть или таймаут (из Docker часто блокируют 587 или IPv6)")
        raise
    except smtplib.SMTPException as e:
        logger.exception("SMTP: отказ сервера")
        raise
    logger.info("SMTP: письмо с кодом отправлено на %s", to_email)


async def send_login_otp(settings: Settings, to_email: str, code: str) -> None:
    """Отправляет одноразовый код. Без smtp_host — только лог (dev)."""
    ttl_min = max(1, settings.join_otp_ttl_seconds // 60)
    if not (settings.smtp_host or "").strip():
        logger.info("SMTP не задан — код входа для %s: %s (только для разработки)", to_email, code)
        return
    _smtp_from_header(settings)  # до сети: явная ошибка конфигурации
    await asyncio.to_thread(_send_otp_sync, settings, to_email, code, ttl_min)
