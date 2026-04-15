import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.config import get_settings
from febnik.db.models import Prize, User
from febnik.services.balance import create_prize_claim
from febnik.services.sheets import append_log_row_async

logger = logging.getLogger(__name__)

router = Router(name="claim")


def _prizes_kb(prizes: list[Prize]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in prizes:
        rows.append(
            [InlineKeyboardButton(text=f"{p.name} ({p.cost_feb} ФЭБ)", callback_data=f"claim:{p.id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("claim"))
async def cmd_claim(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    r = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = r.scalar_one_or_none()
    if not user:
        await message.answer("Сначала /start")
        return

    r2 = await session.execute(select(Prize).where(Prize.stock > 0).order_by(Prize.name))
    prizes = r2.scalars().all()
    if not prizes:
        await message.answer("Сейчас нет доступных призов.")
        return

    await message.answer(
        f"Ваш баланс: {user.balance_feb} ФЭБ. Выберите приз — баллы спишутся сразу, "
        "затем подойдите на стойку и назовите номер заявки активисту.",
        reply_markup=_prizes_kb(prizes),
    )


@router.callback_query(F.data.startswith("claim:"))
async def on_claim_prize(callback: CallbackQuery, session: AsyncSession) -> None:
    if not callback.data or not callback.from_user:
        return
    try:
        pid = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Ошибка данных")
        return

    r = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = r.scalar_one_or_none()
    if not user:
        await callback.answer("Нужна регистрация /start", show_alert=True)
        return

    prize = await session.get(Prize, pid)
    if not prize or prize.stock <= 0:
        await callback.answer("Приз недоступен", show_alert=True)
        return

    try:
        claim, tx = await create_prize_claim(session, user, prize)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return

    settings = get_settings()
    try:
        await append_log_row_async(
            settings,
            when=datetime.now(timezone.utc),
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            delta=tx.delta,
            balance_after=tx.balance_after,
            kind="prize_purchase",
            note=f"Заявка #{claim.id} {prize.name}",
        )
    except Exception:
        logger.exception("sheets log failed")

    await session.flush()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Заявка №{claim.id} оформлена: {prize.name} за {prize.cost_feb} ФЭБ.\n"
        f"Остаток на счёте: {user.balance_feb} ФЭБ.\n"
        "Подойдите на стойку выдачи и покажите этот номер активисту.",
    )
    await callback.answer()
