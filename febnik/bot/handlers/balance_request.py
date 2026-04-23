from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.bot.states import BalanceRequestFlow
from febnik.config import get_settings
from febnik.db.models import BalanceRequest, BalanceRequestStatus, User
from febnik.services.balance import has_pending_balance_request

router = Router(name="balance_request")


@router.message(Command("request"))
async def cmd_request(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user:
        return
    r = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = r.scalar_one_or_none()
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return

    if await has_pending_balance_request(session, user.id):
        await message.answer(
            "У вас уже есть заявка на рассмотрении. Дождитесь решения оргкомитета в веб-панели.",
        )
        return

    await state.set_state(BalanceRequestFlow.enter_amount)
    mx = get_settings().max_balance_request_feb
    await message.answer(
        f"Сколько ФЭБартов вы просите начислить? Введите целое число от 1 до {mx}.\n"
        "Отмена: /cancel",
    )


@router.message(BalanceRequestFlow.enter_amount, F.text)
async def br_enter_amount(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.text:
        return
    mx = get_settings().max_balance_request_feb
    try:
        n = int(message.text.strip().replace(" ", ""))
    except ValueError:
        await message.answer(f"Нужно целое число от 1 до {mx}.")
        return
    if n < 1 or n > mx:
        await message.answer(f"Допустимо от 1 до {mx}.")
        return

    await state.update_data(amount_feb=n)
    await state.set_state(BalanceRequestFlow.enter_comment)
    await message.answer(
        "Кратко опишите причину (например: оплата на стойке, возврат). "
        "Или отправьте «-» без комментария.\n"
        "Отмена: /cancel",
    )


@router.message(BalanceRequestFlow.enter_comment, F.text)
async def br_enter_comment(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    data = await state.get_data()
    amount = data.get("amount_feb")
    if not amount:
        await state.clear()
        await message.answer("Сессия сброшена. Начните с /request")
        return

    r = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = r.scalar_one_or_none()
    if not user:
        await state.clear()
        await message.answer("Сначала /start")
        return

    if await has_pending_balance_request(session, user.id):
        await state.clear()
        await message.answer("Уже есть активная заявка.")
        return

    raw = message.text.strip()
    comment = None if raw in ("-", "—", "") else raw

    br = BalanceRequest(
        user_id=user.id,
        amount_feb=int(amount),
        comment=comment,
        status=BalanceRequestStatus.pending,
    )
    session.add(br)
    await session.flush()
    await state.clear()

    await message.answer(
        f"Заявка №{br.id} отправлена: {br.amount_feb} ФЭБарт.\n"
        "Оргкомитет рассмотрит её в веб-админке. После решения вам придёт сообщение здесь.",
    )
