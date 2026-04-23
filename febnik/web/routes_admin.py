import csv
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from febnik.config import get_settings
from febnik.db.models import (
    Activity,
    BalanceRequest,
    BalanceRequestStatus,
    Claim,
    ClaimStatus,
    Prize,
    Transaction,
    User,
)
from febnik.services.balance import (
    apply_admin_balance_set,
    apply_interactive_reward,
    approve_balance_request,
    reject_balance_request,
)
from febnik.services.qr_token import parse_participant_scan_token
from febnik.services.sheets import append_log_row_async
from febnik.services.telegram_notify import send_user_message
from febnik.services.user_web import is_web_user
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


@router.post("/admin/award-from-qr")
async def admin_award_from_qr(
    request: Request,
    session: DbSession,
    t: str = Form(...),
    activity_id: int = Form(...),
    amount_feb: str = Form(""),
) -> RedirectResponse:
    if not request.session.get("admin"):
        return RedirectResponse(url="/admin/login", status_code=302)
    settings = get_settings()
    uid = parse_participant_scan_token(t.strip())
    if uid is None:
        request.session["flash"] = "Недействительная ссылка начисления."
        return RedirectResponse(url="/admin/", status_code=302)
    user = await session.get(User, uid)
    if not user or not is_web_user(user):
        request.session["flash"] = "Участник не найден или не веб-профиль."
        return RedirectResponse(url="/admin/", status_code=302)
    act = await session.get(Activity, activity_id)
    if not act:
        request.session["flash"] = "Интерактив не найден."
        return RedirectResponse(url="/admin/", status_code=302)

    raw = (amount_feb or "").strip()
    if raw:
        try:
            award_amount = int(raw)
        except ValueError:
            request.session["flash"] = "Укажите целое число ФЭБарт."
            return RedirectResponse(url="/admin/", status_code=302)
    else:
        award_amount = act.reward_feb

    if award_amount < 1:
        request.session["flash"] = "Сумма начисления должна быть не меньше 1 ФЭБарт."
        return RedirectResponse(url="/admin/", status_code=302)
    if award_amount > settings.max_qr_award_feb:
        request.session["flash"] = f"Слишком много: максимум {settings.max_qr_award_feb} ФЭБарт за одно начисление."
        return RedirectResponse(url="/admin/", status_code=302)

    tx = await apply_interactive_reward(
        session,
        user,
        award_amount,
        act.id,
        note=f"Интерактив: {act.name} (QR), заявлено {award_amount} ФЭБарт",
    )
    await session.flush()
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
            kind="interactive_reward",
            note=f"{act.name} (веб QR)",
        )
    except Exception:
        logger.exception("sheets log (award QR)")

    request.session["flash"] = (
        f"Начислено {award_amount} ФЭБарт участнику {user.full_name} за «{act.name}». Баланс: {user.balance_feb}."
    )
    return RedirectResponse(url="/admin/", status_code=302)


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
    w.writerow(["ФИО", "email", "username", "telegram_id", "balance_feb"])
    for u in users:
        w.writerow([u.full_name, u.email or "", u.username or "", u.telegram_id, u.balance_feb])
    data = buf.getvalue().encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="balances.csv"'},
    )
