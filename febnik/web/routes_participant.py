"""Участник: вход по почте (код в письме) → ФИО при первой регистрации; долгоживущая подписанная cookie, кабинет, QR."""

import hmac
import io
import smtplib
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
from febnik.services.mail import looks_like_email, normalize_email, send_login_otp
from febnik.services.qr_token import make_participant_scan_token
from febnik.services.sheets import append_log_row_async
from febnik.services.user_web import create_web_participant, get_web_user_by_email, is_web_user
from febnik.web.deps import DbSession, panel_base_url
from febnik.web.participant_auth import attach_participant, clear_participant, get_participant_user_id
from febnik.web.join_session import (
    JOIN_LAST_SENT,
    JOIN_OTP_ATTEMPTS,
    JOIN_OTP_EXPIRES,
    JOIN_OTP_HASH,
    JOIN_PENDING_EMAIL,
    JOIN_VERIFIED_EMAIL,
    clear_pending_otp,
    generate_otp_code,
    now_ts,
    otp_hash,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _ctx(request: Request, **extra: object):
    return {"request": request, "panel_url": panel_base_url(), **extra}


def _participant_id(request: Request) -> int | None:
    return get_participant_user_id(request)


def _redirect_cabinet_guest(request: Request) -> RedirectResponse:
    """Нет участника или неверный токен — на вход и сброс подписанной cookie."""
    r = RedirectResponse(url="/join", status_code=302)
    clear_participant(r, request)
    return r


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
    smtp_ok = bool((get_settings().smtp_host or "").strip())
    return templates.TemplateResponse(
        "participant/join_email.html",
        _ctx(request, flash_join=flash_join, smtp_configured=smtp_ok),
    )


@router.post("/join")
async def join_post(
    request: Request,
    email: str = Form(...),
) -> RedirectResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    settings = get_settings()
    em = normalize_email(email)
    if not looks_like_email(em):
        request.session["flash_join"] = "Введите корректный адрес электронной почты."
        return RedirectResponse(url="/join", status_code=302)
    now = now_ts()
    last = int(request.session.get(JOIN_LAST_SENT) or 0)
    pending = request.session.get(JOIN_PENDING_EMAIL)
    if pending == em and last and (now - last) < settings.join_otp_resend_seconds:
        request.session["flash_join"] = f"Повторная отправка через {settings.join_otp_resend_seconds} с после предыдущей."
        return RedirectResponse(url="/join/code", status_code=302)
    code = generate_otp_code()
    request.session[JOIN_PENDING_EMAIL] = em
    request.session[JOIN_OTP_HASH] = otp_hash(code, settings.session_secret)
    request.session[JOIN_OTP_EXPIRES] = now + settings.join_otp_ttl_seconds
    request.session[JOIN_LAST_SENT] = now
    request.session[JOIN_OTP_ATTEMPTS] = 0
    request.session.pop(JOIN_VERIFIED_EMAIL, None)
    try:
        await send_login_otp(settings, em, code)
    except (OSError, ValueError, smtplib.SMTPException) as e:
        clear_pending_otp(request.session)
        request.session["flash_join"] = f"Не удалось отправить письмо: {e}"
        return RedirectResponse(url="/join", status_code=302)
    return RedirectResponse(url="/join/code", status_code=302)


@router.get("/join/code", response_class=HTMLResponse)
async def join_code_get(request: Request) -> Response:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    pending = request.session.get(JOIN_PENDING_EMAIL)
    if not pending:
        request.session["flash_join"] = "Сначала укажите почту на шаге входа."
        return RedirectResponse(url="/join", status_code=302)
    flash_join = request.session.pop("flash_join", None)
    smtp_ok = bool((get_settings().smtp_host or "").strip())
    return templates.TemplateResponse(
        "participant/join_code.html",
        _ctx(request, flash_join=flash_join, pending_email=pending, smtp_configured=smtp_ok),
    )


@router.post("/join/resend")
async def join_resend(request: Request) -> RedirectResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    settings = get_settings()
    em = request.session.get(JOIN_PENDING_EMAIL)
    if not em:
        return RedirectResponse(url="/join", status_code=302)
    now = now_ts()
    last = int(request.session.get(JOIN_LAST_SENT) or 0)
    if last and (now - last) < settings.join_otp_resend_seconds:
        wait = settings.join_otp_resend_seconds - (now - last)
        request.session["flash_join"] = f"Подождите ещё {wait} с."
        return RedirectResponse(url="/join/code", status_code=302)
    code = generate_otp_code()
    request.session[JOIN_OTP_HASH] = otp_hash(code, settings.session_secret)
    request.session[JOIN_OTP_EXPIRES] = now + settings.join_otp_ttl_seconds
    request.session[JOIN_LAST_SENT] = now
    request.session[JOIN_OTP_ATTEMPTS] = 0
    try:
        await send_login_otp(settings, em, code)
    except (OSError, ValueError, smtplib.SMTPException) as e:
        request.session["flash_join"] = f"Не удалось отправить письмо: {e}"
    return RedirectResponse(url="/join/code", status_code=302)


@router.post("/join/code")
async def join_code_post(
    request: Request,
    session: DbSession,
    otp: str = Form(...),
) -> RedirectResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    settings = get_settings()
    em = request.session.get(JOIN_PENDING_EMAIL)
    exp = int(request.session.get(JOIN_OTP_EXPIRES) or 0)
    stored_hash = request.session.get(JOIN_OTP_HASH)
    if not em or not stored_hash:
        request.session["flash_join"] = "Сессия истекла. Запросите код снова."
        clear_pending_otp(request.session)
        return RedirectResponse(url="/join", status_code=302)
    now = now_ts()
    if now > exp:
        clear_pending_otp(request.session)
        request.session["flash_join"] = "Срок кода истёк. Запросите новый."
        return RedirectResponse(url="/join", status_code=302)
    attempts = int(request.session.get(JOIN_OTP_ATTEMPTS) or 0) + 1
    request.session[JOIN_OTP_ATTEMPTS] = attempts
    if attempts > 8:
        clear_pending_otp(request.session)
        request.session["flash_join"] = "Слишком много неверных попыток. Запросите код заново."
        return RedirectResponse(url="/join", status_code=302)
    raw_code = (otp or "").strip().replace(" ", "")
    if len(raw_code) != 6 or not raw_code.isdigit():
        request.session["flash_join"] = "Введите 6 цифр из письма."
        return RedirectResponse(url="/join/code", status_code=302)
    if not hmac.compare_digest(stored_hash, otp_hash(raw_code, settings.session_secret)):
        request.session["flash_join"] = "Неверный код."
        return RedirectResponse(url="/join/code", status_code=302)
    clear_pending_otp(request.session)
    user = await get_web_user_by_email(session, em)
    if user and is_web_user(user):
        r = RedirectResponse(url="/cabinet", status_code=302)
        attach_participant(r, request, user.id)
        request.session.pop("flash_join", None)
        return r
    request.session[JOIN_VERIFIED_EMAIL] = em
    request.session.pop("flash_join", None)
    return RedirectResponse(url="/join/name", status_code=302)


@router.get("/join/name", response_class=HTMLResponse)
async def join_name_get(request: Request, session: DbSession) -> Response:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    verified = request.session.get(JOIN_VERIFIED_EMAIL)
    if not verified:
        request.session["flash_join"] = "Сначала подтвердите почту кодом из письма."
        return RedirectResponse(url="/join", status_code=302)
    existing = await get_web_user_by_email(session, verified)
    if existing and is_web_user(existing):
        r = RedirectResponse(url="/cabinet", status_code=302)
        attach_participant(r, request, existing.id)
        request.session.pop(JOIN_VERIFIED_EMAIL, None)
        return r
    flash_join = request.session.pop("flash_join", None)
    return templates.TemplateResponse(
        "participant/join_name.html",
        _ctx(request, flash_join=flash_join, verified_email=verified),
    )


@router.post("/join/name")
async def join_name_post(
    request: Request,
    session: DbSession,
    full_name: str = Form(...),
) -> RedirectResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    verified = request.session.get(JOIN_VERIFIED_EMAIL)
    if not verified:
        request.session["flash_join"] = "Сначала подтвердите почту кодом из письма."
        return RedirectResponse(url="/join", status_code=302)
    existing = await get_web_user_by_email(session, verified)
    if existing and is_web_user(existing):
        r = RedirectResponse(url="/cabinet", status_code=302)
        attach_participant(r, request, existing.id)
        request.session.pop(JOIN_VERIFIED_EMAIL, None)
        return r
    try:
        user = await create_web_participant(session, full_name, verified)
        await session.flush()
    except ValueError as e:
        request.session["flash_join"] = str(e)
        return RedirectResponse(url="/join/name", status_code=302)
    r = RedirectResponse(url="/cabinet", status_code=302)
    attach_participant(r, request, user.id)
    request.session.pop(JOIN_VERIFIED_EMAIL, None)
    request.session.pop("flash_join", None)
    return r


@router.get("/cabinet", response_class=HTMLResponse)
async def cabinet(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return _redirect_cabinet_guest(request)
    flash = request.session.pop("flash_cabinet", None)
    return templates.TemplateResponse(
        "participant/cabinet.html",
        _ctx(request, user=user, flash_cabinet=flash),
    )


@router.get("/cabinet/prizes", response_class=HTMLResponse)
async def cabinet_prizes(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return _redirect_cabinet_guest(request)
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
        return _redirect_cabinet_guest(request)
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
        request.session["flash_cabinet"] = "Заявки на начисление ФЭБарт с сайта отключены."
        return RedirectResponse(url="/cabinet", status_code=302)
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return _redirect_cabinet_guest(request)
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
        return _redirect_cabinet_guest(request)
    mx = get_settings().max_balance_request_feb
    if await has_pending_balance_request(session, user.id):
        request.session["flash_cabinet"] = "У вас уже есть заявка на рассмотрении."
        return RedirectResponse(url="/cabinet/request", status_code=302)
    if amount_feb < 1 or amount_feb > mx:
        request.session["flash_cabinet"] = f"Допустимо от 1 до {mx} ФЭБарт."
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
        return _redirect_cabinet_guest(request)
    base = panel_base_url()
    token = make_participant_scan_token(user.id)
    scan_url = f"{base}/scan?t={token}"
    return templates.TemplateResponse(
        "participant/qr.html",
        _ctx(request, user=user, scan_url=scan_url),
    )


@router.get("/cabinet/logout")
async def cabinet_logout(request: Request) -> RedirectResponse:
    r = RedirectResponse(url="/join", status_code=302)
    clear_participant(r, request)
    return r
