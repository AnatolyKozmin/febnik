"""Участник: регистрация по ФИО и студбилету; повторный вход по студбилету; кабинет, QR."""

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from fastapi import APIRouter, Form, Request
from sqlalchemy.exc import IntegrityError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from febnik.config import get_settings
from febnik.services.cabinet_banners import active_banner_url_path
from febnik.db.models import BalanceRequest, BalanceRequestStatus, Prize, User
from febnik.services.balance import create_prize_claim, has_pending_balance_request
from febnik.services.qr_token import make_participant_scan_token
from febnik.services.sheets import append_log_row_async
from febnik.services.feedback_survey import (
    load_all_slots,
    submit_feedback,
    user_has_response,
)
from febnik.survey_content import get_survey_day
from febnik.services.user_web import (
    create_web_participant,
    get_web_user_by_pin,
    get_web_user_by_student_ticket,
    is_web_user,
)
from febnik.web.deps import DbSession, panel_base_url
from febnik.web.participant_auth import attach_participant, clear_participant, get_participant_user_id
from febnik.web.join_session import RETURN_ATTEMPTS

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
    return templates.TemplateResponse(
        "participant/join_landing.html",
        _ctx(request, flash_join=flash_join),
    )


@router.get("/join/first", response_class=HTMLResponse)
async def join_first_get(request: Request) -> Response:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    flash_join = request.session.pop("flash_join", None)
    return templates.TemplateResponse(
        "participant/join_first.html",
        _ctx(request, flash_join=flash_join),
    )


@router.post("/join/first")
async def join_first_post(
    request: Request,
    session: DbSession,
    full_name: str = Form(...),
    student_ticket: str = Form(...),
) -> RedirectResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    try:
        user = await create_web_participant(session, full_name, student_ticket)
        await session.flush()
    except ValueError as e:
        request.session["flash_join"] = str(e)
        return RedirectResponse(url="/join/first", status_code=302)
    r = RedirectResponse(url="/cabinet", status_code=302)
    attach_participant(r, request, user.id)
    request.session["flash_cabinet"] = "Добро пожаловать! При следующем визите нажмите «Уже регистрировался» и введите тот же номер студенческого билета."
    return r


@router.get("/join/your-code")
async def join_your_code_legacy() -> RedirectResponse:
    return RedirectResponse(url="/join/first", status_code=302)


@router.post("/join/continue")
async def join_continue_legacy() -> RedirectResponse:
    return RedirectResponse(url="/join", status_code=302)


@router.get("/join/return", response_class=HTMLResponse)
async def join_return_get(request: Request) -> Response:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    flash_join = request.session.pop("flash_join", None)
    return templates.TemplateResponse(
        "participant/join_return.html",
        _ctx(request, flash_join=flash_join),
    )


@router.post("/join/return")
async def join_return_post(
    request: Request,
    session: DbSession,
    student_ticket: str = Form(...),
) -> RedirectResponse:
    if _participant_id(request):
        return RedirectResponse(url="/cabinet", status_code=302)
    compact = "".join((student_ticket or "").split())
    if not compact:
        request.session["flash_join"] = "Введите 6 цифр номера студенческого билета."
        return RedirectResponse(url="/join/return", status_code=302)
    att = int(request.session.get(RETURN_ATTEMPTS) or 0) + 1
    request.session[RETURN_ATTEMPTS] = att
    if att > 30:
        request.session["flash_join"] = "Слишком много попыток. Подождите несколько минут или обратитесь к организаторам."
        return RedirectResponse(url="/join/return", status_code=302)
    user = None
    if len(compact) == 6 and compact.isdigit():
        user = await get_web_user_by_student_ticket(session, compact)
    if not user and len(compact) in (4, 6) and compact.isdigit():
        user = await get_web_user_by_pin(session, compact)
    if not user:
        if not compact.isdigit():
            request.session["flash_join"] = "Номер билета — только цифры (6 штук)."
        elif len(compact) == 6:
            request.session["flash_join"] = (
                "Такого номера нет среди зарегистрированных. Проверьте цифры или пройдите регистрацию."
            )
        else:
            request.session["flash_join"] = (
                "Нужны 6 цифр студенческого билета. Если у вас старый вход по коду из 4 цифр — введите только этот код."
            )
        return RedirectResponse(url="/join/return", status_code=302)
    request.session[RETURN_ATTEMPTS] = 0
    r = RedirectResponse(url="/cabinet", status_code=302)
    attach_participant(r, request, user.id)
    request.session.pop("flash_join", None)
    return r


@router.get("/cabinet", response_class=HTMLResponse)
async def cabinet(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return _redirect_cabinet_guest(request)
    flash = request.session.pop("flash_cabinet", None)
    banner = await active_banner_url_path(session, get_settings())
    return templates.TemplateResponse(
        "participant/cabinet.html",
        _ctx(request, user=user, flash_cabinet=flash, cabinet_banner_url=banner),
        headers={"Cache-Control": "private, no-store, max-age=0, must-revalidate"},
    )


@router.get("/cabinet/balance.json")
async def cabinet_balance_json(request: Request, session: DbSession) -> JSONResponse:
    """Лёгкий JSON для обновления баланса без полной перезагрузки (после скана при плохой сети)."""
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return JSONResponse({"ok": False}, status_code=401)
    await session.refresh(user)
    return JSONResponse({"ok": True, "balance_feb": user.balance_feb})


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


@router.get("/cabinet/feedback", response_class=HTMLResponse)
async def cabinet_feedback_landing(request: Request, session: DbSession) -> Response:
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return _redirect_cabinet_guest(request)
    slots = await load_all_slots(session)
    done = {d: await user_has_response(session, user.id, d) for d in (1, 2, 3)}
    flash = request.session.pop("flash_cabinet", None)
    return templates.TemplateResponse(
        "participant/feedback_landing.html",
        _ctx(request, user=user, slots=slots, done=done, flash_cabinet=flash),
    )


@router.get("/cabinet/feedback/{day}", response_class=HTMLResponse)
async def cabinet_feedback_form(request: Request, session: DbSession, day: int) -> Response:
    if day not in (1, 2, 3):
        return RedirectResponse(url="/cabinet/feedback", status_code=302)
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return _redirect_cabinet_guest(request)
    survey = get_survey_day(day)
    if not survey:
        request.session["flash_cabinet"] = "Анкета для этого дня не настроена."
        return RedirectResponse(url="/cabinet/feedback", status_code=302)
    slots = await load_all_slots(session)
    slot = slots[day]
    if not slot.is_open:
        request.session["flash_cabinet"] = f"Анкета «День {day}» сейчас закрыта."
        return RedirectResponse(url="/cabinet/feedback", status_code=302)
    if await user_has_response(session, user.id, day):
        request.session["flash_cabinet"] = "Вы уже отправили ответы за этот день."
        return RedirectResponse(url="/cabinet/feedback", status_code=302)
    flash = request.session.pop("flash_cabinet", None)
    # Явно передаём поля и финальный текст — шаблон не зависит от одного имени `survey`
    # (избегает UndefinedError при частичном деплое старого роутера + нового шаблона).
    return templates.TemplateResponse(
        "participant/feedback_form.html",
        _ctx(
            request,
            user=user,
            day=day,
            slot=slot,
            feedback_fields=survey.fields,
            feedback_closing=survey.closing_message,
            flash_cabinet=flash,
        ),
    )


@router.post("/cabinet/feedback/{day}")
async def cabinet_feedback_post(
    request: Request,
    session: DbSession,
    day: int,
    answers_json: str = Form(...),
) -> RedirectResponse:
    if day not in (1, 2, 3):
        return RedirectResponse(url="/cabinet/feedback", status_code=302)
    user = await _load_participant(session, request)
    if not user or not is_web_user(user):
        return _redirect_cabinet_guest(request)
    try:
        raw = json.loads(answers_json or "{}")
    except json.JSONDecodeError:
        request.session["flash_cabinet"] = "Не удалось прочитать ответы. Обновите страницу и попробуйте снова."
        return RedirectResponse(url=f"/cabinet/feedback/{day}", status_code=302)
    if not isinstance(raw, dict):
        request.session["flash_cabinet"] = "Некорректный формат ответов."
        return RedirectResponse(url=f"/cabinet/feedback/{day}", status_code=302)
    try:
        _, granted = await submit_feedback(session, user, day, raw)
        await session.flush()
    except IntegrityError:
        await session.rollback()
        request.session["flash_cabinet"] = "Этот ответ уже был сохранён ранее."
        return RedirectResponse(url="/cabinet/feedback", status_code=302)
    except ValueError as e:
        request.session["flash_cabinet"] = str(e)
        return RedirectResponse(url=f"/cabinet/feedback/{day}", status_code=302)
    if granted:
        settings = get_settings()
        try:
            await append_log_row_async(
                settings,
                when=datetime.now(timezone.utc),
                telegram_id=user.telegram_id,
                username=user.username,
                full_name=user.full_name,
                delta=granted,
                balance_after=user.balance_feb,
                kind="feedback_reward",
                note=f"Анкета ОС день {day}",
            )
        except Exception:
            pass
    msg = "Спасибо! Анкета отправлена."
    if granted:
        msg += f" Начислено {granted} ФЭБарт."
    request.session["flash_cabinet"] = msg
    return RedirectResponse(url="/cabinet/feedback", status_code=302)


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
