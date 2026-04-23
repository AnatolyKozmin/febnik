"""Отправка писем (код входа участника). При пустом SMTP_HOST код только пишется в лог — для локальной разработки."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import socket
import ssl
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


def _smtp_connect_target(settings: Settings) -> tuple[str, str]:
    """(адрес для TCP, имя хоста для проверки TLS после STARTTLS)."""
    host = (settings.smtp_host or "").strip()
    port = settings.smtp_port
    if not settings.smtp_prefer_ipv4:
        return host, host
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            ip = infos[0][4][0]
            logger.info("SMTP: соединение по IPv4 %s вместо %s", ip, host)
            return ip, host
    except OSError as e:
        logger.warning("SMTP: не удалось взять IPv4 для %s, остаёмся на имени: %s", host, e)
    return host, host


def _smtp_ssl_client_ipv4(
    logical_host: str,
    port: int,
    timeout: float,
) -> smtplib.SMTP:
    """TLS с первого байта (порт 465): TCP на IPv4, SNI/сертификат по logical_host (как smtp.gmail.com)."""
    infos = socket.getaddrinfo(logical_host, port, socket.AF_INET, socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"Нет IPv4 для {logical_host}:{port}")
    ip = infos[0][4][0]
    logger.info("SMTP SSL: IPv4 %s → TLS (имя сертификата %s)", ip, logical_host)
    raw = socket.create_connection((ip, port), timeout=timeout)
    ctx = ssl.create_default_context()
    ssock = ctx.wrap_socket(raw, server_hostname=logical_host)
    smtp = smtplib.SMTP(timeout=timeout)
    smtp.sock = ssock
    smtp.file = ssock.makefile("rb")
    smtp._host = logical_host
    (code, resp) = smtp.getreply()
    if code != 220:
        raise smtplib.SMTPConnectError(code, resp)
    return smtp


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
    logical = (settings.smtp_host or "").strip()
    port = settings.smtp_port
    timeout = 60.0

    try:
        if settings.smtp_implicit_ssl:
            logger.info("SMTP: режим SSL с порта (порт %s), login=%s", port, bool(user))
            if settings.smtp_prefer_ipv4:
                smtp = _smtp_ssl_client_ipv4(logical, port, timeout)
                try:
                    if user:
                        smtp.login(user, pwd)
                        logger.info("SMTP: авторизация ок")
                    smtp.send_message(msg)
                finally:
                    try:
                        smtp.quit()
                    except Exception:
                        smtp.close()
            else:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(logical, port, timeout=int(timeout), context=ctx) as smtp:
                    if user:
                        smtp.login(user, pwd)
                        logger.info("SMTP: авторизация ок")
                    smtp.send_message(msg)
        else:
            connect_host, tls_name = _smtp_connect_target(settings)
            logger.info(
                "SMTP: подключение %s:%s STARTTLS=%s login=%s",
                connect_host,
                port,
                settings.smtp_starttls,
                bool(user),
            )
            with smtplib.SMTP(connect_host, port, timeout=int(timeout)) as smtp:
                if settings.smtp_starttls:
                    ctx = ssl.create_default_context()
                    smtp.starttls(context=ctx, server_hostname=tls_name)
                    logger.info("SMTP: STARTTLS готов")
                if user:
                    smtp.login(user, pwd)
                    logger.info("SMTP: авторизация ок")
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        logger.exception("SMTP: ошибка авторизации (проверьте пароль приложения и SMTP_USER)")
        raise
    except OSError:
        logger.exception("SMTP: сеть или таймаут (587/465, файрвол, IPv6)")
        raise
    except smtplib.SMTPException:
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
