from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.bot.help_text import build_help_text
from febnik.bot.states import Registration
from febnik.bot.utils import norm_username
from febnik.db.models import User, UserRole

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user:
        return
    tg = message.from_user
    r = await session.execute(select(User).where(User.telegram_id == tg.id))
    user = r.scalar_one_or_none()

    if user is None:
        await state.set_state(Registration.waiting_fio)
        await message.answer(
            "Привет! Это бот валюты «ФЭБарт» проекта ФЭБник.\n"
            "Отправьте одним сообщением ваши ФИО (как в документах).",
        )
        return

    user.username = norm_username(tg.username)
    await session.flush()
    await message.answer(
        f"Снова здравствуйте, {user.full_name}!\n\n{build_help_text(tg.id)}",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Сценарий отменён. Справка: /start")


@router.message(Registration.waiting_fio, F.text)
async def process_fio(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    fio = message.text.strip()
    if len(fio) < 3:
        await message.answer("ФИО слишком короткое. Напишите полностью.")
        return

    tg = message.from_user
    user = User(
        telegram_id=tg.id,
        username=norm_username(tg.username),
        full_name=fio,
        role=UserRole.participant,
        balance_feb=0,
    )
    session.add(user)
    await session.flush()
    await state.clear()
    await message.answer(
        f"Регистрация завершена: {fio}\n"
        f"Ваш Telegram: @{tg.username or 'без username'} — при желании укажите @username в настройках Telegram.\n\n"
        f"{build_help_text(tg.id)}",
    )
