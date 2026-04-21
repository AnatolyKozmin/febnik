"""Участник: вход по ФИО, сессия в cookie, кабинет, заявки, QR."""

import io
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from febnik.config import get_settings
from febnik.db.models import BalanceRequest, BalanceRequestStatus, Prize, User
from febnik.services.balance import create_prize_claim, has_pending_balance_request
from febnik.services.qr_token import make_participant_scan_token
from febnik.services.sheets import append_log_row_async
from febnik.services.user_web import create_web_participant, is_web_user
from febnik.web.deps import DbSession, panel_base_url
PARTICIPANT_SESSION_KEY = "participant_user_id"

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _ctx(request: Request, **extra: object):
    return {"request": request, "panel_url": panel_base_url(), **extra}


def _participant_id(request: Request) -> int | None:
    v = request.session.get(PARTICIPANT_SESSION_KEY)
    return int(v) if v is not None else None


async def _load_participant(session: DbSession, request: Request) -> User | None:
    uid = _participant_id(request)
    if not uid:
        return None
    return await session.get(User, uid)


@router.get("/join", response_class=HTMLResponse)
async def join_get(request: Request) -> HTMLResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    flash_join = request.session.pop("flash_join", None)
    return templates.TemplateResponse(
        "participant/join.html",
        _ctx(request, flash_join=flash_join),
    )


@router.post("/join")
async def join_post(
    request: Request,
    session: DbSession,
    full_name: str = Form(...),
) -> RedirectResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    try:
        user = await create_web_participant(session, full_name)
        await session.flush()
    except ValueError as e:
        request.session["flash_join"] = str(e)
        return RedirectResponse(url="/join", status_code=302)
    request.session[PARTICIPANT_SESSION_KEY] = user.id
    request.session.pop("flash_join", None)
    return RedirectResponse(url="/cabinet", status_code=302)


@router.get("/cabinet", response_class=HTMLResponse)
async def cabinet(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user:
        return RedirectResponse(url="/join", status_code=302)
    if not is_web_user(user):
        # Сессия от старого Telegram-пользователя — сброс
        request.session.pop(PARTICIPANT_SESSION_KEY, None)
        return RedirectResponse(url="/join", status_code=302)
    flash = request.session.pop("flash_cabinet", None)
    return templates.TemplateResponse(
        "participant/cabinet.html",
        _ctx(request, user=user, flash_cabinet=flash),
    )


@router.get("/cabinet/prizes", response_class=HTMLResponse)
async def cabinet_prizes(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return RedirectResponse(url="/join", status_code=302)
    r = await session.execute(select(Prize).where(Prize.stock > 0).order_by(Prize.cost_feb, Prize.name))
    prizes = list(r.scalars().all())
    flash_prizes = request.session.pop("flash_cabinet", None)
    return templates.TemplateResponse(
        "participant/prizes.html",
        _ctx(request, user=user, prizes=prizes, flash_prizes=flash_prizes),
    )


@router.post("/cabinet/claim/{prize_id}")
async def cabinet_claim(
    request: Request,
    session: DbSession,
    prize_id: int,
) -> RedirectResponse:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return RedirectResponse(url="/join", status_code=302)
    prize = await session.get(Prize, prize_id)
    if not prize or prize.stock <= 0:
        request.session["flash_cabinet"] = "Приз недоступен."
        return RedirectResponse(url="/cabinet/prizes", status_code=302)
    try:
        claim, tx = await create_prize_claim(session, user, prize)
    except ValueError as e:
        request.session["flash_cabinet"] = str(e)
        return RedirectResponse(url="/cabinet/prizes", status_code=302)
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
            kind="prize_purchase",
            note=f"Заявка #{claim.id} {prize.name} (веб)",
        )
    except Exception:
        pass
    request.session["flash_cabinet"] = (
        f"Заявка №{claim.id} оформлена: «{prize.name}». Подойдите на стойку и назовите номер активисту."
    )
    return RedirectResponse(url="/cabinet", status_code=302)


@router.get("/cabinet/request", response_class=HTMLResponse)
async def cabinet_request_get(request: Request, session: DbSession) -> Response:
    if not get_settings().web_balance_request_enabled:
        request.session["flash_cabinet"] = "Заявки на начисление ФЭБ с сайта отключены."
        return RedirectResponse(url="/cabinet", status_code=302)
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return RedirectResponse(url="/join", status_code=302)
    pending = await has_pending_balance_request(session, user.id)
    mx = get_settings().max_balance_request_feb
    return templates.TemplateResponse(
        "participant/request.html",
        _ctx(request, user=user, pending=pending, max_feb=mx),
    )


@router.post("/cabinet/request")
async def cabinet_request_post(
    request: Request,
    session: DbSession,
    amount_feb: int = Form(...),
    comment: str = Form(""),
) -> RedirectResponse:
    if not get_settings().web_balance_request_enabled:
        return RedirectResponse(url="/cabinet", status_code=302)
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return RedirectResponse(url="/join", status_code=302)
    mx = get_settings().max_balance_request_feb
    if await has_pending_balance_request(session, user.id):
        request.session["flash_cabinet"] = "У вас уже есть заявка на рассмотрении."
        return RedirectResponse(url="/cabinet/request", status_code=302)
    if amount_feb < 1 or amount_feb > mx:
        request.session["flash_cabinet"] = f"Допустимо от 1 до {mx} ФЭБ."
        return RedirectResponse(url="/cabinet/request", status_code=302)
    raw = (comment or "").strip()
    cmt = None if raw in ("-", "—", "") else raw[:2000]
    br = BalanceRequest(
        user_id=user.id,
        amount_feb=amount_feb,
        comment=cmt,
        status=BalanceRequestStatus.pending,
    )
    session.add(br)
    await session.flush()
    request.session["flash_cabinet"] = f"Заявка №{br.id} отправлена оргкомитету."
    return RedirectResponse(url="/cabinet", status_code=302)


@router.get("/cabinet/qr.png")
async def cabinet_qr_png(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return Response(status_code=403)
    base = panel_base_url()
    token = make_participant_scan_token(user.id)
    scan_url = f"{base}/scan?t={token}"
    img = qrcode.make(scan_url, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/cabinet/qr", response_class=HTMLResponse)
async def cabinet_qr_page(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return RedirectResponse(url="/join", status_code=302)
    base = panel_base_url()
    token = make_participant_scan_token(user.id)
    scan_url = f"{base}/scan?t={token}"
    return templates.TemplateResponse(
        "participant/qr.html",
        _ctx(request, user=user, scan_url=scan_url),
    )


@router.get("/cabinet/logout")
async def cabinet_logout(request: Request) -> RedirectResponse:
    request.session.pop(PARTICIPANT_SESSION_KEY, None)
    return RedirectResponse(url="/join", status_code=302)
