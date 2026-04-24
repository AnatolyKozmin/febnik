"""Плашки кабинета участника по дням мероприятия (загрузка в админке)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.config import Settings
from febnik.db.models import CabinetDayBanner, WebAppState

WEB_STATE_ID = 1
ALLOWED_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_FILE_BYTES = 4 * 1024 * 1024


def resolve_banners_root(settings: Settings) -> Path:
    raw = (settings.cabinet_banners_dir or "").strip()
    p = Path(raw) if raw else Path("uploads/cabinet_banners")
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def ensure_banners_dir(settings: Settings) -> Path:
    root = resolve_banners_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def get_or_create_web_state(session: AsyncSession) -> WebAppState:
    s = await session.get(WebAppState, WEB_STATE_ID)
    if s:
        return s
    s = WebAppState(id=WEB_STATE_ID, cabinet_banner_active_day=None)
    session.add(s)
    await session.flush()
    return s


async def active_banner_url_path(session: AsyncSession, settings: Settings) -> str | None:
    """Относительный URL для <img> или None, если плашку не показываем."""
    state = await get_or_create_web_state(session)
    d = state.cabinet_banner_active_day
    if d not in (1, 2, 3):
        return None
    rec = await session.get(CabinetDayBanner, d)
    if not rec:
        return None
    root = resolve_banners_root(settings)
    if not (root / rec.file_name).is_file():
        return None
    return f"/media/cabinet-banners/{rec.file_name}"


async def save_day_banner(session: AsyncSession, day: int, upload: UploadFile, settings: Settings) -> None:
    if day not in (1, 2, 3):
        raise ValueError("День должен быть 1, 2 или 3.")
    orig = upload.filename or ""
    suf = Path(orig).suffix.lower()
    if suf not in ALLOWED_SUFFIX:
        raise ValueError("Допустимы файлы: PNG, JPEG, WebP, GIF.")
    body = await upload.read()
    if len(body) > MAX_FILE_BYTES:
        raise ValueError("Файл больше 4 МБ.")
    if len(body) == 0:
        raise ValueError("Пустой файл.")
    root = ensure_banners_dir(settings)
    fn = f"day{day}_{uuid.uuid4().hex[:16]}{suf}"
    path = root / fn
    rec = await session.get(CabinetDayBanner, day)
    if rec:
        old = root / rec.file_name
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
        rec.file_name = fn
    else:
        session.add(CabinetDayBanner(day=day, file_name=fn))
    path.write_bytes(body)
    await session.flush()


async def all_day_banner_urls(session: AsyncSession, settings: Settings) -> dict[int, str | None]:
    """Превью в админке: день → URL или None."""
    out: dict[int, str | None] = {1: None, 2: None, 3: None}
    r = await session.execute(select(CabinetDayBanner))
    root = resolve_banners_root(settings)
    for row in r.scalars().all():
        if row.day in out and (root / row.file_name).is_file():
            out[row.day] = f"/media/cabinet-banners/{row.file_name}"
    return out
