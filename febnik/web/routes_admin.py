import asyncio
import csv
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from febnik.config import get_settings
from febnik.db.models import (
    Activity,
    BalanceRequest,
    BalanceRequestStatus,
    CabinetDayBanner,
    Claim,
    ClaimStatus,
    FeedbackSurveySlot,
    Prize,
    Transaction,
    User,
)
from febnik.services.balance import (
    apply_admin_balance_set,
    approve_balance_request,
    reject_balance_request,
)
from febnik.services.qr_award import QrAwardOk, admin_try_award_from_qr
from febnik.services.sheets import (
    append_log_row_async,
    export_feedback_day_to_sheet_async,
    feedback_sheet_tab_title,
)
from febnik.services.telegram_notify import send_user_message
from febnik.services.cabinet_banners import (
    all_day_banner_urls,
    get_or_create_web_state,
    resolve_banners_root,
    save_day_banner,
)
from febnik.services.feedback_survey import (
    ensure_feedback_slots,
    list_responses_for_day,
    load_all_slots,
)
from febnik.survey_content import format_answers_for_admin
from febnik.services.user_web import normalize_student_ticket_optional
from febnik.web.deps import DbSession, panel_base_url

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _parse_date(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _ctx(request: Request, **extra: object):
    flash = request.session.pop("flash", None)
    base = {"request": request, "flash": flash, **extra}
    return base


def _safe_redirect_path(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    if not s.startswith("/") or s.startswith("//"):
        return None
    return s


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_get(
    request: Request,
    next: Annotated[str | None, Query(alias="next")] = None,
) -> HTMLResponse:
    if request.session.get("admin"):
        target = _safe_redirect_path(next) or "/admin/"
        return RedirectResponse(url=target, status_code=302)
    return templates.TemplateResponse(
        "admin/login.html",
        _ctx(request, next_after_login=_safe_redirect_path(next)),
    )


@router.post("/admin/login")
async def admin_login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
) -> RedirectResponse:
    s = get_settings()
    if username == s.admin_username and password == s.admin_password:
        request.session["admin"] = True
        request.session["flash"] = "Вход выполнен."
        target = _safe_redirect_path(next) or "/admin/"
        return RedirectResponse(url=target, status_code=302)
    request.session["flash"] = "Неверный логин или пароль."
    fail = "/admin/login"
    sn = _safe_redirect_path(next)
    if sn:
        fail += "?" + urlencode({"next": sn})
    return RedirectResponse(url=fail, status_code=302)


@router.get("/admin/logout")
async def admin_logout(request: Request) -> RedirectResponse:
    request.session.pop("admin", None)
    return RedirectResponse(url="/admin/login", status_code=302)


class AwardFromQrJsonBody(BaseModel):
    t: str = ""
    amount_feb: int = Field(ge=1)
    idempotency_key: str = ""


async def _append_qr_award_sheet_log_async(settings, outcome: QrAwardOk) -> None:
    if outcome.replay:
        return
    u = outcome.user
    tx = outcome.tx
    try:
        await append_log_row_async(
            settings,
            when=datetime.now(timezone.utc),
            telegram_id=u.telegram_id,
            username=u.username,
            full_name=u.full_name,
            delta=tx.delta,
            balance_after=tx.balance_after,
            kind="interactive_reward",
            note="Скан QR (ручной ввод)",
        )
    except Exception:
        logger.exception("sheets log (award QR)")


@router.post("/admin/award-from-qr")
async def admin_award_from_qr(
    request: Request,
    session: DbSession,
    t: str = Form(...),
    amount_feb: str = Form(...),
    idempotency_key: str = Form(""),
) -> RedirectResponse:
    if not request.session.get("admin"):
        return RedirectResponse(url="/admin/login", status_code=302)
    settings = get_settings()
    raw = (amount_feb or "").strip()
    try:
        award_amount = int(raw)
    except ValueError:
        request.session["flash"] = "Укажите целое число ФЭБарт."
        return RedirectResponse(url="/scan?" + urlencode({"t": t.strip()}), status_code=302)

    out = await admin_try_award_from_qr(
        session,
        token=t,
        award_amount=award_amount,
        idempotency_key=idempotency_key,
    )
    if isinstance(out, str):
        if "Недействительная" in out or "не найден" in out or "веб-профиль" in out:
            request.session["flash"] = out
            return RedirectResponse(url="/admin/scan", status_code=302)
        request.session["flash"] = out
        return RedirectResponse(url="/scan?" + urlencode({"t": t.strip()}), status_code=302)

    await _append_qr_award_sheet_log_async(settings, out)
    if out.replay:
        request.session["flash"] = (
            f"Это начисление уже было сохранено ранее (повтор запроса). "
            f"{out.user.full_name}: баланс {out.user.balance_feb} ФЭБарт. Можно сканировать следующий билет."
        )
    else:
        request.session["flash"] = (
            f"Начислено {award_amount} ФЭБарт — {out.user.full_name}. "
            f"Баланс: {out.user.balance_feb}. Сканируйте следующий билет."
        )
    return RedirectResponse(url="/admin/scan", status_code=302)


@router.post("/admin/api/award-from-qr")
async def admin_api_award_from_qr(
    request: Request,
    session: DbSession,
    body: AwardFromQrJsonBody,
) -> JSONResponse:
    if not request.session.get("admin"):
        return JSONResponse({"ok": False, "error": "auth", "message": "Нужен вход в админку."}, status_code=401)
    if not (body.t or "").strip():
        return JSONResponse(
            {"ok": False, "error": "validation", "message": "Не указан код из QR."},
            status_code=400,
        )
    settings = get_settings()
    mx = settings.max_qr_award_feb
    if body.amount_feb > mx:
        return JSONResponse(
            {
                "ok": False,
                "error": "validation",
                "message": f"Слишком много: максимум {mx} ФЭБарт за одно начисление.",
            },
            status_code=400,
        )
    out = await admin_try_award_from_qr(
        session,
        token=body.t,
        award_amount=body.amount_feb,
        idempotency_key=body.idempotency_key,
    )
    if isinstance(out, str):
        code = "validation"
        status = 400
        if "Недействительная" in out or "не найден" in out or "веб-профиль" in out:
            code = "token"
            status = 400
        return JSONResponse({"ok": False, "error": code, "message": out}, status=status)
    await _append_qr_award_sheet_log_async(settings, out)
    return JSONResponse(
        {
            "ok": True,
            "duplicate": out.replay,
            "amount_feb": body.amount_feb,
            "balance_after": out.user.balance_feb,
            "full_name": out.user.full_name,
        }
    )


@router.get("/admin/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session: DbSession) -> HTMLResponse:
    nu = await session.scalar(select(func.count(User.id)))
    na = await session.scalar(select(func.count(Activity.id)))
    np = await session.scalar(select(func.count(Prize.id)))
    pending = await session.scalar(
        select(func.count(Claim.id)).where(Claim.status == ClaimStatus.awaiting_handout)
    )
    pending_br = await session.scalar(
        select(func.count(BalanceRequest.id)).where(BalanceRequest.status == BalanceRequestStatus.pending)
    )
    settings = get_settings()
    return templates.TemplateResponse(
        "admin/dashboard.html",
        _ctx(
            request,
            users_count=nu or 0,
            activities_count=na or 0,
            prizes_count=np or 0,
            pending_claims=pending or 0,
            pending_balance_requests=pending_br or 0,
            panel_url=panel_base_url(),
            bot_enabled=settings.bot_enabled,
        ),
    )


@router.get("/admin/cabinet-banners", response_class=HTMLResponse)
async def admin_cabinet_banners_get(request: Request, session: DbSession) -> HTMLResponse:
    settings = get_settings()
    state = await get_or_create_web_state(session)
    previews = await all_day_banner_urls(session, settings)
    return templates.TemplateResponse(
        "admin/cabinet_banners.html",
        _ctx(
            request,
            active_day=state.cabinet_banner_active_day,
            previews=previews,
        ),
    )


@router.post("/admin/cabinet-banners/active")
async def admin_cabinet_banners_set_active(
    request: Request,
    session: DbSession,
    active_day: str = Form(""),
) -> RedirectResponse:
    st = await get_or_create_web_state(session)
    raw = (active_day or "").strip().lower()
    if raw in ("", "none", "0"):
        st.cabinet_banner_active_day = None
    elif raw in ("1", "2", "3"):
        d = int(raw)
        st.cabinet_banner_active_day = d
        rec = await session.get(CabinetDayBanner, d)
        settings = get_settings()
        if not rec:
            request.session["flash"] = f"Сначала загрузите картинку для дня {d}."
            return RedirectResponse(url="/admin/cabinet-banners", status_code=302)
        if not (resolve_banners_root(settings) / rec.file_name).is_file():
            request.session["flash"] = f"Файл плашки дня {d} не найден на диске. Загрузите снова."
            return RedirectResponse(url="/admin/cabinet-banners", status_code=302)
    else:
        request.session["flash"] = "Некорректный выбор дня."
        return RedirectResponse(url="/admin/cabinet-banners", status_code=302)
    await session.flush()
    if st.cabinet_banner_active_day is None:
        request.session["flash"] = "Плашка в кабинете отключена."
    else:
        request.session["flash"] = f"В кабинете показывается плашка дня {st.cabinet_banner_active_day}."
    return RedirectResponse(url="/admin/cabinet-banners", status_code=302)


@router.post("/admin/cabinet-banners/upload")
async def admin_cabinet_banners_upload(
    request: Request,
    session: DbSession,
    day: int = Form(...),
    file: UploadFile = File(...),
) -> RedirectResponse:
    if day not in (1, 2, 3):
        request.session["flash"] = "День должен быть 1, 2 или 3."
        return RedirectResponse(url="/admin/cabinet-banners", status_code=302)
    try:
        await save_day_banner(session, day, file, get_settings())
        await session.flush()
    except ValueError as e:
        request.session["flash"] = str(e)
        return RedirectResponse(url="/admin/cabinet-banners", status_code=302)
    request.session["flash"] = f"Картинка для дня {day} сохранена."
    return RedirectResponse(url="/admin/cabinet-banners", status_code=302)


@router.get("/admin/feedback", response_class=HTMLResponse)
async def admin_feedback_get(
    request: Request,
    session: DbSession,
    day: int = Query(1, ge=1, le=3),
) -> HTMLResponse:
    await ensure_feedback_slots(session)
    slots = await load_all_slots(session)
    responses = await list_responses_for_day(session, day)
    response_items = [
        {"row": r, "pairs": format_answers_for_admin(day, r.answers_json)}
        for r in responses
    ]
    settings = get_settings()
    return templates.TemplateResponse(
        "admin/feedback.html",
        _ctx(
            request,
            slots=slots,
            active_tab=day,
            response_items=response_items,
            max_qr_award_feb=settings.max_qr_award_feb,
            feedback_google_configured=bool(
                settings.google_credentials_path and settings.google_spreadsheet_id
            ),
            feedback_sheet_tab_title=feedback_sheet_tab_title(settings, day),
        ),
    )


@router.post("/admin/feedback/slot")
async def admin_feedback_slot_post(
    request: Request,
    session: DbSession,
    day: int = Form(...),
    is_open: str = Form(...),
    reward_feb: int = Form(0),
    title: str = Form(""),
) -> RedirectResponse:
    if day not in (1, 2, 3):
        request.session["flash"] = "День должен быть 1, 2 или 3."
        return RedirectResponse(url="/admin/feedback", status_code=302)
    await ensure_feedback_slots(session)
    slot = await session.get(FeedbackSurveySlot, day)
    if not slot:
        request.session["flash"] = "Слот анкеты не найден."
        return RedirectResponse(url="/admin/feedback", status_code=302)
    slot.is_open = (is_open or "").strip() == "1"
    mx = get_settings().max_qr_award_feb
    try:
        rw = int(reward_feb)
    except (TypeError, ValueError):
        rw = 0
    slot.reward_feb = max(0, min(rw, mx))
    raw_t = (title or "").strip()
    slot.title = raw_t[:512] if raw_t else None
    await session.flush()
    request.session["flash"] = f"Анкета «День {day}» обновлена."
    return RedirectResponse(url=f"/admin/feedback?day={day}", status_code=302)


@router.post("/admin/feedback/export-google")
async def admin_feedback_export_google(
    request: Request,
    session: DbSession,
    day: int = Form(...),
) -> RedirectResponse:
    if day not in (1, 2, 3):
        request.session["flash"] = "День должен быть 1, 2 или 3."
        return RedirectResponse(url="/admin/feedback", status_code=302)
    settings = get_settings()
    responses = await list_responses_for_day(session, day)
    responses_sorted = sorted(
        responses,
        key=lambda r: (r.created_at.timestamp() if r.created_at else 0.0, r.id),
    )
    row_tuples: list[tuple[datetime | None, str, str, str, str | None]] = []
    for r in responses_sorted:
        u = r.user
        if u is None:
            continue
        row_tuples.append(
            (
                r.created_at,
                u.full_name,
                u.email or "",
                u.student_ticket or "",
                r.answers_json,
            )
        )
    err = await export_feedback_day_to_sheet_async(settings, day, row_tuples)
    tab = feedback_sheet_tab_title(settings, day)
    if err:
        request.session["flash"] = f"Google: {err}"
    else:
        request.session["flash"] = f"Выгружено {len(row_tuples)} ответ(ов) на вкладку «{tab}»."
    return RedirectResponse(url=f"/admin/feedback?day={day}", status_code=302)


@router.get("/admin/scan", response_class=HTMLResponse)
async def admin_scan_page(request: Request) -> HTMLResponse:
    """Точка входа «сканера»: вставить ссылку из QR участника или токен t."""
    return templates.TemplateResponse(
        "admin/scan.html",
        _ctx(request, panel_url=panel_base_url()),
    )


@router.get("/admin/activities", response_class=HTMLResponse)
async def admin_activities(request: Request, session: DbSession) -> HTMLResponse:
    r = await session.execute(select(Activity).order_by(Activity.event_date.desc().nulls_last(), Activity.name))
    rows = list(r.scalars().all())
    return templates.TemplateResponse("admin/activities.html", _ctx(request, activities=rows))


@router.get("/admin/activities/new", response_class=HTMLResponse)
async def admin_activity_new_get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("admin/activity_form.html", _ctx(request, activity=None, title="Новый интерактив"))


@router.post("/admin/activities/new")
async def admin_activity_new_post(
    request: Request,
    session: DbSession,
    name: str = Form(...),
    reward_feb: int = Form(0),
    event_date: str = Form(""),
    time_text: str = Form(""),
    responsible_username: str = Form(""),
) -> RedirectResponse:
    ru = responsible_username.strip().lstrip("@").lower() or None
    a = Activity(
        sheet_row=None,
        name=name.strip(),
        reward_feb=max(0, reward_feb),
        event_date=_parse_date(event_date),
        time_text=time_text.strip() or None,
        responsible_username=ru,
    )
    session.add(a)
    request.session["flash"] = "Интерактив создан."
    return RedirectResponse(url="/admin/activities", status_code=302)


@router.get("/admin/activities/{aid}/edit", response_class=HTMLResponse)
async def admin_activity_edit_get(request: Request, session: DbSession, aid: int) -> HTMLResponse:
    a = await session.get(Activity, aid)
    if not a:
        request.session["flash"] = "Не найдено."
        return RedirectResponse(url="/admin/activities", status_code=302)
    return templates.TemplateResponse(
        "admin/activity_form.html",
        _ctx(request, activity=a, title="Редактирование"),
    )


@router.post("/admin/activities/{aid}/edit")
async def admin_activity_edit_post(
    request: Request,
    session: DbSession,
    aid: int,
    name: str = Form(...),
    reward_feb: int = Form(0),
    event_date: str = Form(""),
    time_text: str = Form(""),
    responsible_username: str = Form(""),
) -> RedirectResponse:
    a = await session.get(Activity, aid)
    if not a:
        request.session["flash"] = "Не найдено."
        return RedirectResponse(url="/admin/activities", status_code=302)
    ru = responsible_username.strip().lstrip("@").lower() or None
    a.name = name.strip()
    a.reward_feb = max(0, reward_feb)
    a.event_date = _parse_date(event_date)
    a.time_text = time_text.strip() or None
    a.responsible_username = ru
    request.session["flash"] = "Сохранено."
    return RedirectResponse(url="/admin/activities", status_code=302)


@router.post("/admin/activities/{aid}/delete")
async def admin_activity_delete(request: Request, session: DbSession, aid: int) -> RedirectResponse:
    a = await session.get(Activity, aid)
    if a:
        await session.delete(a)
    request.session["flash"] = "Удалено."
    return RedirectResponse(url="/admin/activities", status_code=302)


@router.get("/admin/prizes", response_class=HTMLResponse)
async def admin_prizes(request: Request, session: DbSession) -> HTMLResponse:
    r = await session.execute(select(Prize).order_by(Prize.name))
    rows = list(r.scalars().all())
    return templates.TemplateResponse("admin/prizes.html", _ctx(request, prizes=rows))


@router.get("/admin/prizes/new", response_class=HTMLResponse)
async def admin_prize_new_get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("admin/prize_form.html", _ctx(request, prize=None, title="Новый приз"))


@router.post("/admin/prizes/new")
async def admin_prize_new_post(
    request: Request,
    session: DbSession,
    name: str = Form(...),
    cost_feb: int = Form(...),
    stock: int = Form(0),
) -> RedirectResponse:
    p = Prize(sheet_row=None, name=name.strip(), cost_feb=max(0, cost_feb), stock=max(0, stock))
    session.add(p)
    request.session["flash"] = "Приз добавлен."
    return RedirectResponse(url="/admin/prizes", status_code=302)


@router.get("/admin/prizes/{pid}/edit", response_class=HTMLResponse)
async def admin_prize_edit_get(request: Request, session: DbSession, pid: int) -> HTMLResponse:
    p = await session.get(Prize, pid)
    if not p:
        request.session["flash"] = "Не найдено."
        return RedirectResponse(url="/admin/prizes", status_code=302)
    return templates.TemplateResponse("admin/prize_form.html", _ctx(request, prize=p, title="Редактирование приза"))


@router.post("/admin/prizes/{pid}/edit")
async def admin_prize_edit_post(
    request: Request,
    session: DbSession,
    pid: int,
    name: str = Form(...),
    cost_feb: int = Form(...),
    stock: int = Form(0),
) -> RedirectResponse:
    p = await session.get(Prize, pid)
    if not p:
        request.session["flash"] = "Не найдено."
        return RedirectResponse(url="/admin/prizes", status_code=302)
    p.name = name.strip()
    p.cost_feb = max(0, cost_feb)
    p.stock = max(0, stock)
    request.session["flash"] = "Сохранено."
    return RedirectResponse(url="/admin/prizes", status_code=302)


@router.post("/admin/prizes/{pid}/delete")
async def admin_prize_delete(request: Request, session: DbSession, pid: int) -> RedirectResponse:
    p = await session.get(Prize, pid)
    if not p:
        request.session["flash"] = "Не найдено."
        return RedirectResponse(url="/admin/prizes", status_code=302)
    cnt = await session.scalar(select(func.count(Claim.id)).where(Claim.prize_id == pid))
    if cnt and cnt > 0:
        request.session["flash"] = "Нельзя удалить: есть заявки на этот приз."
        return RedirectResponse(url="/admin/prizes", status_code=302)
    await session.delete(p)
    request.session["flash"] = "Удалено."
    return RedirectResponse(url="/admin/prizes", status_code=302)


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, session: DbSession) -> HTMLResponse:
    r = await session.execute(select(User).order_by(User.full_name))
    rows = list(r.scalars().all())
    return templates.TemplateResponse("admin/users.html", _ctx(request, users=rows))


@router.post("/admin/users/{uid}/set-balance")
async def admin_user_set_balance(
    request: Request,
    session: DbSession,
    uid: int,
    new_balance: int = Form(...),
) -> RedirectResponse:
    user = await session.get(User, uid)
    if not user:
        request.session["flash"] = "Участник не найден."
        return RedirectResponse(url="/admin/users", status_code=302)
    try:
        tx = await apply_admin_balance_set(session, user, new_balance)
        await session.flush()
    except ValueError as e:
        request.session["flash"] = str(e)
        return RedirectResponse(url="/admin/users", status_code=302)
    settings = get_settings()
    if tx:
        try:
            await append_log_row_async(
                settings,
                when=datetime.now(timezone.utc),
                telegram_id=user.telegram_id,
                username=user.username,
                full_name=user.full_name,
                delta=tx.delta,
                balance_after=tx.balance_after,
                kind="admin_adjust",
                note=tx.note or "",
            )
        except Exception:
            logger.exception("sheets log (admin balance set)")
        request.session["flash"] = (
            f"Баланс {user.full_name}: {user.balance_feb} ФЭБарт (изменение {tx.delta:+d})."
        )
    else:
        request.session["flash"] = "Значение не изменилось."
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/admin/users/{uid}/set-student-ticket")
async def admin_user_set_student_ticket(
    request: Request,
    session: DbSession,
    uid: int,
    student_ticket: str = Form(""),
) -> RedirectResponse:
    user = await session.get(User, uid)
    if not user:
        request.session["flash"] = "Участник не найден."
        return RedirectResponse(url="/admin/users", status_code=302)
    try:
        ticket = normalize_student_ticket_optional(student_ticket)
    except ValueError as e:
        request.session["flash"] = str(e)
        return RedirectResponse(url="/admin/users", status_code=302)
    if ticket is None:
        user.student_ticket = None
    else:
        dup = await session.execute(
            select(User.id).where(User.student_ticket == ticket, User.id != uid)
        )
        if dup.scalar_one_or_none() is not None:
            request.session["flash"] = "Такой номер студенческого билета уже указан у другого участника."
            return RedirectResponse(url="/admin/users", status_code=302)
        user.student_ticket = ticket
    await session.flush()
    request.session["flash"] = f"Студенческий билет для «{user.full_name}» сохранён."
    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/admin/transactions", response_class=HTMLResponse)
async def admin_transactions(request: Request, session: DbSession) -> HTMLResponse:
    r = await session.execute(
        select(Transaction)
        .options(selectinload(Transaction.user))
        .order_by(Transaction.created_at.desc())
        .limit(200)
    )
    rows = list(r.scalars().all())
    return templates.TemplateResponse("admin/transactions.html", _ctx(request, transactions=rows))


@router.get("/admin/claims", response_class=HTMLResponse)
async def admin_claims(request: Request, session: DbSession) -> HTMLResponse:
    r = await session.execute(
        select(Claim)
        .options(selectinload(Claim.user), selectinload(Claim.prize))
        .order_by(Claim.created_at.desc())
        .limit(100)
    )
    rows = list(r.scalars().all())
    return templates.TemplateResponse("admin/claims.html", _ctx(request, claims=rows))


@router.get("/admin/balance-requests", response_class=HTMLResponse)
async def admin_balance_requests(request: Request, session: DbSession) -> HTMLResponse:
    r1 = await session.execute(
        select(BalanceRequest)
        .options(selectinload(BalanceRequest.user))
        .where(BalanceRequest.status == BalanceRequestStatus.pending)
        .order_by(BalanceRequest.created_at)
    )
    pending_rows = list(r1.scalars().all())
    r2 = await session.execute(
        select(BalanceRequest)
        .options(selectinload(BalanceRequest.user))
        .where(BalanceRequest.status != BalanceRequestStatus.pending)
        .order_by(BalanceRequest.resolved_at.desc().nulls_last(), BalanceRequest.id.desc())
        .limit(80)
    )
    history = list(r2.scalars().all())
    return templates.TemplateResponse(
        "admin/balance_requests.html",
        _ctx(request, pending_rows=pending_rows, history=history),
    )


@router.post("/admin/balance-requests/{rid}/approve")
async def admin_balance_request_approve(request: Request, session: DbSession, rid: int) -> RedirectResponse:
    req = await session.get(BalanceRequest, rid)
    if not req:
        request.session["flash"] = "Заявка не найдена."
        return RedirectResponse(url="/admin/balance-requests", status_code=302)
    try:
        await approve_balance_request(session, req)
        await session.flush()
    except ValueError as e:
        request.session["flash"] = str(e)
        return RedirectResponse(url="/admin/balance-requests", status_code=302)
    user = await session.get(User, req.user_id)
    if user and user.telegram_id > 0:
        await send_user_message(
            user.telegram_id,
            f"Заявка №{rid} одобрена: начислено {req.amount_feb} ФЭБарт. "
            f"Ваш баланс: {user.balance_feb} ФЭБарт.",
        )
        request.session["flash"] = f"Заявка №{rid} одобрена, участник уведомлён в Telegram."
    else:
        request.session["flash"] = f"Заявка №{rid} одобрена (без уведомления в Telegram — веб-участник)."
    return RedirectResponse(url="/admin/balance-requests", status_code=302)


@router.post("/admin/balance-requests/{rid}/reject")
async def admin_balance_request_reject(
    request: Request,
    session: DbSession,
    rid: int,
    reason: str = Form(""),
) -> RedirectResponse:
    req = await session.get(BalanceRequest, rid)
    if not req:
        request.session["flash"] = "Заявка не найдена."
        return RedirectResponse(url="/admin/balance-requests", status_code=302)
    try:
        await reject_balance_request(session, req, reason)
        await session.flush()
    except ValueError as e:
        request.session["flash"] = str(e)
        return RedirectResponse(url="/admin/balance-requests", status_code=302)
    user = await session.get(User, req.user_id)
    if user and user.telegram_id > 0:
        text = f"Заявка №{rid} на {req.amount_feb} ФЭБарт отклонена."
        rr = (reason or "").strip()
        if rr:
            text += f" Комментарий: {rr}"
        await send_user_message(user.telegram_id, text)
        request.session["flash"] = f"Заявка №{rid} отклонена, участник уведомлён в Telegram."
    else:
        request.session["flash"] = f"Заявка №{rid} отклонена."
    return RedirectResponse(url="/admin/balance-requests", status_code=302)


@router.get("/admin/export/balances.csv")
async def export_balances_csv(session: DbSession) -> Response:
    r = await session.execute(select(User).order_by(User.full_name))
    users = r.scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ФИО", "email", "student_ticket", "username", "telegram_id", "balance_feb"])
    for u in users:
        w.writerow([u.full_name, u.email or "", u.student_ticket or "", u.username or "", u.telegram_id, u.balance_feb])
    data = buf.getvalue().encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="balances.csv"'},
    )


@router.get("/admin/export/participants.xlsx")
async def export_participants_xlsx(session: DbSession) -> Response:
    """Выгрузка всех пользователей в Excel (без секретных полей вроде PIN)."""
    r = await session.execute(select(User).order_by(User.full_name))
    users = r.scalars().all()
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Участники"
    ws.append(
        [
            "ID",
            "ФИО",
            "Email",
            "Студбилет",
            "Username (TG)",
            "Telegram ID",
            "Роль",
            "ФЭБарт",
            "Создан (UTC)",
        ]
    )
    for u in users:
        ws.append(
            [
                u.id,
                u.full_name,
                u.email or "",
                u.student_ticket or "",
                u.username or "",
                int(u.telegram_id),
                u.role.value,
                u.balance_feb,
                u.created_at.replace(tzinfo=None) if u.created_at else None,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="participants.xlsx"'},
    )
