from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from febnik.bot.commands_setup import ensure_staff_commands_menu


class StaffCommandsMiddleware(BaseMiddleware):
    """После первого сообщения от пользователя чат существует — тогда можно setMyCommands(chat)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot = data.get("bot")
        uid: int | None = None
        if isinstance(event, Message) and event.from_user:
            uid = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            uid = event.from_user.id
        if bot is not None and uid is not None:
            await ensure_staff_commands_menu(bot, uid)
        return await handler(event, data)
