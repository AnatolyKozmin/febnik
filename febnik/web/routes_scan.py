"""Публичная ссылка из QR: начисление ФЭБарт организатором (после входа в админку)."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from urllib.parse import urlencode

from febnik.db.models import Activity, User
from febnik.services.qr_token import parse_participant_scan_token
from febnik.services.user_web import is_web_user
from febnik.config import get_settings
from febnik.web.deps import DbSession, panel_base_url

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _ctx(request: Request, **extra: object):
    return {"request": request, "panel_url": panel_base_url(), **extra}


@router.get("/scan", response_class=HTMLResponse)
async def scan_qr_landing(request: Request, session: DbSession, t: str = "") -> HTMLResponse:
    if not t or not t.strip():
        return templates.TemplateResponse(
            "scan/invalid.html",
            _ctx(request, reason="Не указан код."),
        )
    uid = parse_participant_scan_token(t.strip())
    if uid is None:
        return templates.TemplateResponse(
            "scan/invalid.html",
            _ctx(request, reason="Ссылка устарела или повреждена."),
        )
    user = await session.get(User, uid)
    if not user:
        return templates.TemplateResponse(
            "scan/invalid.html",
            _ctx(request, reason="Участник не найден."),
        )
    if not is_web_user(user):
        return templates.TemplateResponse(
            "scan/invalid.html",
            _ctx(request, reason="Этот код не для веб-участника."),
        )

    r = await session.execute(select(Activity).order_by(Activity.event_date, Activity.name))
    activities = list(r.scalars().all())
    mx = get_settings().max_qr_award_feb

    is_admin = bool(request.session.get("admin"))
    login_url = "/admin/login?" + urlencode({"next": f"/scan?t={t.strip()}"})

    return templates.TemplateResponse(
        "scan/award.html",
        _ctx(
            request,
            user=user,
            token=t.strip(),
            activities=activities,
            is_admin=is_admin,
            login_url=login_url,
            max_qr_award_feb=mx,
        ),
    )
