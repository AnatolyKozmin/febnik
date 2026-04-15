import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.bot.states import Award
from febnik.bot.utils import norm_username
from febnik.config import can_handout, get_settings, is_org
from febnik.db.models import Activity, Claim, ClaimStatus, Prize, User
from febnik.services.balance import apply_interactive_reward, get_user_by_username, mark_claim_handed_out
from febnik.services.sheets import append_log_row_async, export_balances_to_sheet, sync_activities_from_sheet, sync_prizes_from_sheet
from febnik.services import sheets as sheets_svc

logger = logging.getLogger(__name__)

router = Router(name="staff")


def _can_run_activity(staff_tg_id: int, staff_username: str | None, activity: Activity) -> bool:
    if is_org(staff_tg_id):
        return True
    su = norm_username(staff_username)
    ar = activity.responsible_username
    if not su or not ar:
        return False
    return su == ar.lower()


async def _activities_for_staff(session: AsyncSession, message: Message) -> list[Activity]:
    if not message.from_user:
        return []
    uid = message.from_user.id
    r = await session.execute(select(Activity).order_by(Activity.event_date, Activity.name))
    all_a = list(r.scalars().all())
    if is_org(uid):
        return all_a
    un = norm_username(message.from_user.username)
    if not un:
        return []
    return [a for a in all_a if a.responsible_username and a.responsible_username.lower() == un]


def _award_kb(activities: list[Activity]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in activities[:40]:
        label = a.name[:40] + ("…" if len(a.name) > 40 else "")
        rows.append([InlineKeyboardButton(text=label, callback_data=f"aw:{a.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("sync"))
async def cmd_sync(message: Message, session: AsyncSession) -> None:
    if not message.from_user or not is_org(message.from_user.id):
        await message.answer("Команда только для оргкомитета.")
        return
    settings = get_settings()
    if not settings.google_spreadsheet_id or not settings.google_credentials_path:
        from febnik.web.deps import panel_base_url

        await message.answer(
            "Google Sheets не подключены. Расписание и призы заводите в веб-панели: "
            f"{panel_base_url()}/admin/ (разделы «Интерактивы» и «Призы»).",
        )
        return
    try:
        await asyncio.to_thread(sheets_svc.ensure_log_sheet, settings)
    except Exception as e:
        logger.warning("ensure_log_sheet: %s", e)
    try:
        na = await sync_activities_from_sheet(session, settings)
        np = await sync_prizes_from_sheet(session, settings)
        await message.answer(f"Синхронизация готова: интерактивов {na}, призов {np}.")
    except Exception as e:
        logger.exception("sync")
        await message.answer(f"Ошибка синхронизации: {e}")


@router.message(Command("export_balances"))
async def cmd_export_balances(message: Message, session: AsyncSession) -> None:
    if not message.from_user or not is_org(message.from_user.id):
        await message.answer("Команда только для оргкомитета.")
        return
    settings = get_settings()
    if not settings.google_spreadsheet_id or not settings.google_credentials_path:
        from febnik.web.deps import panel_base_url

        await message.answer(
            f"Экспорт в Google не настроен. Скачайте CSV в панели: {panel_base_url()}/admin/export/balances.csv",
        )
        return
    r = await session.execute(select(User))
    users = r.scalars().all()
    rows = [(u.full_name, u.username, u.telegram_id, u.balance_feb) for u in users]
    try:
        await asyncio.to_thread(export_balances_to_sheet, settings, rows)
        await message.answer(f"Вкладка «Балансы_бот» обновлена: {len(rows)} участников.")
    except Exception as e:
        logger.exception("export")
        await message.answer(f"Ошибка экспорта: {e}")


@router.message(Command("award"))
async def cmd_award(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user:
        return
    acts = await _activities_for_staff(session, message)
    if not acts:
        await message.answer(
            "У вас нет закреплённых интерактивов. В админке в карточке интерактива в поле «Ответственный» "
            "должен быть ваш Telegram @username. Оргкомитет может начислять за любой интерактив.",
        )
        return
    if len(acts) == 1:
        await state.set_state(Award.enter_username)
        await state.update_data(activity_id=acts[0].id)
        await message.answer(
            f"Интерактив: {acts[0].name}\n"
            f"Награда: {acts[0].reward_feb} ФЭБ.\n"
            "Введите @username участника или его ник без @.",
        )
        return
    await state.set_state(Award.pick_activity)
    await message.answer("Выберите интерактив:", reply_markup=_award_kb(acts))


@router.callback_query(Award.pick_activity, F.data.startswith("aw:"))
async def cb_award_pick(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not callback.data or not callback.from_user:
        return
    aid = int(callback.data.split(":", 1)[1])
    act = await session.get(Activity, aid)
    if not act:
        await callback.answer("Не найдено", show_alert=True)
        return
    if not _can_run_activity(callback.from_user.id, callback.from_user.username, act):
        await callback.answer("Нет доступа к этому интерактиву", show_alert=True)
        return
    await state.set_state(Award.enter_username)
    await state.update_data(activity_id=aid)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"Интерактив: {act.name}\nНаграда: {act.reward_feb} ФЭБ.\nВведите @username или ник участника.",
    )
    await callback.answer()


@router.message(Award.enter_username, F.text)
async def award_enter_username(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    data = await state.get_data()
    aid = data.get("activity_id")
    if not aid:
        await state.clear()
        await message.answer("Сессия сброшена. Начните с /award")
        return
    act = await session.get(Activity, int(aid))
    if not act or not _can_run_activity(message.from_user.id, message.from_user.username, act):
        await state.clear()
        await message.answer("Нет доступа или интерактив удалён.")
        return

    raw = message.text.strip()
    target = await get_user_by_username(session, raw)
    if not target:
        await message.answer("Участник не найден. Пусть нажмёт /start и зарегистрируется, и проверьте ник.")
        return

    tx = await apply_interactive_reward(session, target, act.reward_feb, act.id, note=f"Интерактив: {act.name}")
    await session.flush()

    settings = get_settings()
    try:
        await append_log_row_async(
            settings,
            when=datetime.now(timezone.utc),
            telegram_id=target.telegram_id,
            username=target.username,
            full_name=target.full_name,
            delta=tx.delta,
            balance_after=tx.balance_after,
            kind="interactive_reward",
            note=act.name,
        )
    except Exception:
        logger.exception("sheets log")

    await state.clear()
    await message.answer(f"Начислено {act.reward_feb} ФЭБ пользователю {target.full_name}.")
    try:
        await message.bot.send_message(
            target.telegram_id,
            f"Вы прошли интерактив «{act.name}» и получили {act.reward_feb} ФЭБ. "
            f"Баланс: {target.balance_feb} ФЭБ.",
        )
    except Exception:
        logger.warning("notify user failed tg=%s", target.telegram_id)


@router.message(Command("handout"))
async def cmd_handout(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    if not can_handout(message.from_user.id):
        await message.answer(
            "Подтверждение выдачи только для стойки призов. "
            "Админ добавляет ваш Telegram ID в HANDOUT_TELEGRAM_IDS (или в ORG_TELEGRAM_IDS, если отдельный список пуст).",
        )
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /handout номер_заявки")
        return
    try:
        cid = int(parts[1])
    except ValueError:
        await message.answer("Номер заявки должен быть числом.")
        return

    claim = await session.get(Claim, cid)
    if not claim:
        await message.answer("Заявка не найдена.")
        return
    if claim.status != ClaimStatus.awaiting_handout:
        await message.answer("Заявка уже обработана.")
        return

    user = await session.get(User, claim.user_id)
    prize = await session.get(Prize, claim.prize_id)

    await mark_claim_handed_out(session, claim, message.from_user.id)
    await session.flush()
    await message.answer(
        f"Заявка №{claim.id} отмечена как выданная: {user.full_name if user else '?'} — {prize.name if prize else 'приз'}.",
    )
    if user:
        try:
            await message.bot.send_message(
                user.telegram_id,
                f"Заявка №{claim.id} закрыта: приятного использования приза «{prize.name if prize else ''}»!",
            )
        except Exception:
            logger.warning("notify claim user failed")
