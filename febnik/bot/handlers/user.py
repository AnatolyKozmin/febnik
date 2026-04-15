from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.db.models import Activity, Prize, User

router = Router(name="user")


@router.message(Command("score"))
async def cmd_score(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    r = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = r.scalar_one_or_none()
    if not user:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    await message.answer(f"У вас {user.balance_feb} ФЭБартов.")


@router.message(Command("activities"))
async def cmd_activities(message: Message, session: AsyncSession) -> None:
    today = date.today()
    r = await session.execute(
        select(Activity)
        .where(Activity.event_date == today)
        .order_by(Activity.time_text.nulls_last(), Activity.name)
    )
    rows = list(r.scalars().all())
    header = f"На сегодня ({today.strftime('%d.%m.%Y')}):\n"
    if not rows:
        r2 = await session.execute(select(Activity).order_by(Activity.event_date, Activity.name).limit(30))
        rows = list(r2.scalars().all())
        header = f"На сегодня в расписании пусто. Все загруженные строки из таблицы:\n"
    if not rows:
        await message.answer(
            "Расписание пока пустое. Оргкомитет добавляет интерактивы в веб-админке (см. ссылку в /start).",
        )
        return
    lines = [header]
    for a in rows:
        d = a.event_date.strftime("%d.%m.%Y") if a.event_date else "—"
        t = a.time_text or ""
        lines.append(f"• {a.name} — {d} {t} — {a.reward_feb} ФЭБ")
    await message.answer("\n".join(lines))


@router.message(Command("prizes"))
async def cmd_prizes(message: Message, session: AsyncSession) -> None:
    r = await session.execute(select(Prize).order_by(Prize.cost_feb, Prize.name))
    prizes = r.scalars().all()
    if not prizes:
        await message.answer("Призов пока нет. Оргкомитет добавляет их в веб-админке (см. /start).")
        return
    lines = ["Призы (стоимость в ФЭБартах, остаток):\n"]
    for p in prizes:
        lines.append(f"• {p.name} — {p.cost_feb} ФЭБ, в наличии: {p.stock}")
    lines.append("\nОформление: /claim")
    await message.answer("\n".join(lines))
