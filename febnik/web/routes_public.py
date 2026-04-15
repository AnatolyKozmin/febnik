from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from febnik.db.models import Activity, Prize
from febnik.web.deps import DbSession, panel_base_url

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _ctx(request: Request, **extra: object):
    return {"request": request, "panel_url": panel_base_url(), **extra}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("home.html", _ctx(request))


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
