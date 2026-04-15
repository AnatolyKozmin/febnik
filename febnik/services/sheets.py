"""Синхронизация с Google Таблицами и запись лога."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from febnik.config import Settings, get_settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


def _client(settings: Settings) -> Any:
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.debug("gspread/google-auth не установлены — Google Sheets отключены")
        return None
    if not settings.google_credentials_path or not settings.google_spreadsheet_id:
        return None
    creds = Credentials.from_service_account_file(settings.google_credentials_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_int(s: Any, default: int = 0) -> int:
    if s is None or s == "":
        return default
    try:
        return int(float(str(s).replace(",", ".").split(".")[0]))
    except (ValueError, TypeError):
        return default


def fetch_activities_rows(settings: Settings) -> list[dict[str, Any]]:
    """Столбцы: A=Дата, B=Время, C=Название, D=Награда (ФЭБ), E=Ответственный (@username или без)."""
    client = _client(settings)
    if not client:
        return []
    sh = client.open_by_key(settings.google_spreadsheet_id)
    ws = sh.worksheet(settings.sheet_activities)
    rows = ws.get_all_values()
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 3:
            continue
        name = (row[2] if len(row) > 2 else "").strip()
        if not name:
            continue
        resp = ""
        if len(row) > 4:
            resp = row[4].strip().lstrip("@").lower()
        out.append(
            {
                "sheet_row": i,
                "event_date": _parse_date(row[0] if len(row) > 0 else ""),
                "time_text": (row[1] if len(row) > 1 else "").strip() or None,
                "name": name,
                "reward_feb": _to_int(row[3] if len(row) > 3 else 0, 0),
                "responsible_username": resp or None,
            }
        )
    return out


def fetch_prizes_rows(settings: Settings) -> list[dict[str, Any]]:
    """Столбцы: A=Название, B=Стоимость, C=Остаток."""
    client = _client(settings)
    if not client:
        return []
    sh = client.open_by_key(settings.google_spreadsheet_id)
    ws = sh.worksheet(settings.sheet_prizes)
    rows = ws.get_all_values()
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 2:
            continue
        name = row[0].strip()
        if not name:
            continue
        out.append(
            {
                "sheet_row": i,
                "name": name,
                "cost_feb": _to_int(row[1] if len(row) > 1 else 0, 0),
                "stock": _to_int(row[2] if len(row) > 2 else 0, 0),
            }
        )
    return out


def append_log_row(
    settings: Settings,
    *,
    when: datetime,
    telegram_id: int,
    username: str | None,
    full_name: str,
    delta: int,
    balance_after: int,
    kind: str,
    note: str,
) -> None:
    import gspread

    client = _client(settings)
    if not client:
        return
    try:
        sh = client.open_by_key(settings.google_spreadsheet_id)
        ws = sh.worksheet(settings.sheet_log)
    except gspread.exceptions.WorksheetNotFound:
        sh = client.open_by_key(settings.google_spreadsheet_id)
        ws = sh.add_worksheet(title=settings.sheet_log, rows=1000, cols=10)
        ws.append_row(["Время (UTC)", "Telegram ID", "Username", "ФИО", "Δ ФЭБ", "Баланс после", "Тип", "Комментарий"])
    ws.append_row(
        [
            when.isoformat(),
            str(telegram_id),
            username or "",
            full_name,
            str(delta),
            str(balance_after),
            kind,
            note[:500],
        ]
    )


async def append_log_row_async(settings: Settings, **kwargs: Any) -> None:
    await asyncio.to_thread(append_log_row, settings, **kwargs)


def ensure_log_sheet(settings: Settings) -> None:
    import gspread

    client = _client(settings)
    if not client:
        return
    sh = client.open_by_key(settings.google_spreadsheet_id)
    try:
        sh.worksheet(settings.sheet_log)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=settings.sheet_log, rows=2000, cols=10)
        ws.append_row(["Время (UTC)", "Telegram ID", "Username", "ФИО", "Δ ФЭБ", "Баланс после", "Тип", "Комментарий"])


async def sync_activities_from_sheet(session, settings: Settings | None = None) -> int:
    from sqlalchemy import select

    from febnik.db.models import Activity, Transaction

    settings = settings or get_settings()
    rows = await asyncio.to_thread(fetch_activities_rows, settings)
    incoming_rows = {r["sheet_row"] for r in rows}
    for r in rows:
        existing = await session.scalar(select(Activity).where(Activity.sheet_row == r["sheet_row"]))
        if existing:
            existing.event_date = r["event_date"]
            existing.time_text = r["time_text"]
            existing.name = r["name"]
            existing.reward_feb = r["reward_feb"]
            existing.responsible_username = r["responsible_username"]
        else:
            session.add(
                Activity(
                    sheet_row=r["sheet_row"],
                    event_date=r["event_date"],
                    time_text=r["time_text"],
                    name=r["name"],
                    reward_feb=r["reward_feb"],
                    responsible_username=r["responsible_username"],
                )
            )
    from sqlalchemy import false as sql_false

    if incoming_rows:
        orphan = await session.scalars(
            select(Activity).where(Activity.sheet_row.isnot(None), Activity.sheet_row.not_in(incoming_rows))
        )
    else:
        orphan = await session.scalars(select(Activity).where(sql_false()))
    for act in orphan.all():
        used = await session.scalar(select(Transaction.id).where(Transaction.activity_id == act.id).limit(1))
        if used is None:
            await session.delete(act)
    await session.flush()
    return len(rows)


async def sync_prizes_from_sheet(session, settings: Settings | None = None) -> int:
    from sqlalchemy import select

    from febnik.db.models import Claim, Prize

    settings = settings or get_settings()
    rows = await asyncio.to_thread(fetch_prizes_rows, settings)
    incoming = {r["sheet_row"] for r in rows}
    for r in rows:
        existing = await session.scalar(select(Prize).where(Prize.sheet_row == r["sheet_row"]))
        if existing:
            existing.name = r["name"]
            existing.cost_feb = r["cost_feb"]
            existing.stock = r["stock"]
        else:
            session.add(
                Prize(
                    sheet_row=r["sheet_row"],
                    name=r["name"],
                    cost_feb=r["cost_feb"],
                    stock=r["stock"],
                )
            )
    from sqlalchemy import false as sql_false

    if incoming:
        prize_orphan = await session.scalars(
            select(Prize).where(Prize.sheet_row.isnot(None), Prize.sheet_row.not_in(incoming))
        )
    else:
        prize_orphan = await session.scalars(select(Prize).where(sql_false()))
    for pr in prize_orphan.all():
        has_claim = await session.scalar(select(Claim.id).where(Claim.prize_id == pr.id).limit(1))
        if has_claim is None:
            await session.delete(pr)
    await session.flush()
    return len(rows)


def export_balances_to_sheet(settings: Settings, rows: list[tuple[str, str | None, int, int]]) -> None:
    """rows: (full_name, username, telegram_id, balance_feb) — вкладка «Балансы_бот»."""
    import gspread

    client = _client(settings)
    if not client:
        return
    sh = client.open_by_key(settings.google_spreadsheet_id)
    title = "Балансы_бот"
    try:
        ws = sh.worksheet(title)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=max(500, len(rows) + 5), cols=6)
    ws.append_row(["ФИО", "Username", "Telegram ID", "ФЭБарты"])
    for full_name, username, tg_id, bal in sorted(rows, key=lambda x: x[0].lower()):
        ws.append_row([full_name, username or "", str(tg_id), str(bal)])
