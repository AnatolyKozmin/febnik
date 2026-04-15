"""Меню команд слева от поля ввода (Bot API setMyCommands).

У всех — участнический набор (scope default). Расширенное меню для орг/стойки
задаётся в личном scope после первого сообщения пользователя (см. StaffCommandsMiddleware):
до этого Telegram отвечает «chat not found».
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from febnik.config import get_settings

logger = logging.getLogger(__name__)

# Участники (глобально для личных чатов)
PARTICIPANT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Регистрация и справка по боту"),
    BotCommand(command="score", description="Сколько ФЭБартов на балансе"),
    BotCommand(command="activities", description="Интерактивы и расписание на сегодня"),
    BotCommand(command="prizes", description="Призы, цены и остатки"),
    BotCommand(command="claim", description="Оформить приз (спишутся ФЭБ)"),
    BotCommand(command="request", description="Заявка на начисление ФЭБ (решение в админке)"),
    BotCommand(command="cancel", description="Отменить текущий ввод (ФИО, заявку и т.д.)"),
]

STAFF_EXTRA_COMMANDS: list[BotCommand] = [
    BotCommand(command="award", description="Начислить ФЭБ за интерактив (ответственный / орг)"),
    BotCommand(command="handout", description="Отметить выдачу приза по номеру заявки"),
    BotCommand(command="sync", description="Синхронизация с Google Таблицами (оргкомитет)"),
    BotCommand(
        command="export_balances",
        description="Выгрузить балансы в Google Таблицу (оргкомитет)",
    ),
]

_staff_menu_applied: set[int] = set()


def _full_staff_commands() -> list[BotCommand]:
    return PARTICIPANT_COMMANDS + STAFF_EXTRA_COMMANDS


async def setup_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(PARTICIPANT_COMMANDS, scope=BotCommandScopeDefault())
        logger.info("Базовое меню команд: %s шт.", len(PARTICIPANT_COMMANDS))
    except Exception:
        logger.exception("setMyCommands (default) не выполнен")


async def ensure_staff_commands_menu(bot: Bot, telegram_id: int) -> None:
    settings = get_settings()
    staff_ids = settings.org_ids | settings.handout_ids
    if telegram_id not in staff_ids:
        return
    if telegram_id in _staff_menu_applied:
        return
    try:
        await bot.set_my_commands(
            _full_staff_commands(),
            scope=BotCommandScopeChat(chat_id=telegram_id),
        )
        _staff_menu_applied.add(telegram_id)
        logger.info("Расширенное меню команд для staff user_id=%s", telegram_id)
    except TelegramBadRequest as e:
        text = (getattr(e, "message", None) or str(e)).lower()
        if "chat not found" in text:
            logger.debug(
                "Меню для user_id=%s отложено (редкий случай до инициализации чата): %s",
                telegram_id,
                e,
            )
        else:
            logger.warning("setMyCommands(chat) для user_id=%s: %s", telegram_id, e)
