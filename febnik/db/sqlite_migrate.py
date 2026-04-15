"""Лёгкие правки схемы SQLite для существующих БД (без Alembic)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def apply_sqlite_migrations(connection: AsyncConnection) -> None:
    if connection.dialect.name != "sqlite":
        return
    try:
        r = await connection.execute(text("PRAGMA table_info(transactions)"))
        rows = r.fetchall()
    except Exception:
        return
    cols = {row[1] for row in rows}
    if cols and "balance_request_id" not in cols:
        await connection.execute(text("ALTER TABLE transactions ADD COLUMN balance_request_id INTEGER"))
