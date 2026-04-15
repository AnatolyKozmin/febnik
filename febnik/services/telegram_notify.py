"""Разовая отправка сообщения пользователю (из веб-админки)."""

import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from febnik.config import get_settings

logger = logging.getLogger(__name__)


async def send_user_message(telegram_id: int, text: str) -> None:
    s = get_settings()
    session = AiohttpSession(
        timeout=s.telegram_request_timeout,
        proxy=s.telegram_proxy or None,
    )
    bot = Bot(s.bot_token, session=session)
    try:
        await bot.send_message(telegram_id, text)
    except Exception:
        logger.exception("Не удалось отправить сообщение tg_id=%s", telegram_id)
    finally:
        await bot.session.close()
