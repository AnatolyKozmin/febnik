from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from febnik.db.models import Activity, Prize
from febnik.web.deps import DbSession, panel_base_url

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="8" fill="#0c3229"/>'
    b'<path d="M8 22c4-8 10-12 16-12" stroke="#c49a4a" stroke-width="2" fill="none"/>'
    b"</svg>"
)


def _ctx(request: Request, **extra: object):
    return {"request": request, "panel_url": panel_base_url(), **extra}


@router.get("/favicon.ico")
async def favicon_ico() -> Response:
    return Response(
        content=_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> RedirectResponse:
    if request.session.get("participant_user_id"):
        return RedirectResponse(url="/cabinet", status_code=302)
    return RedirectResponse(url="/join", status_code=302)


@router.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request, session: DbSession) -> HTMLResponse:
    today = date.today()
    r = await session.execute(
        select(Activity)
        .where(Activity.event_date == today)
        .order_by(Activity.time_text.nulls_last(), Activity.name)
    )
    rows = list(r.scalars().all())
    if not rows:
        r2 = await session.execute(select(Activity).order_by(Activity.event_date, Activity.name).limit(50))
        rows = list(r2.scalars().all())
        empty_today = True
    else:
        empty_today = False
    return templates.TemplateResponse(
        "schedule.html",
        _ctx(request, activities=rows, today=today, empty_today=empty_today),
    )


@router.get("/prizes-view", response_class=HTMLResponse)
async def prizes_view(request: Request, session: DbSession) -> HTMLResponse:
    r = await session.execute(select(Prize).order_by(Prize.cost_feb, Prize.name))
    prizes = list(r.scalars().all())
    return templates.TemplateResponse("prizes_public.html", _ctx(request, prizes=prizes))
