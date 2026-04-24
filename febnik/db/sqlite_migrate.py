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

    try:
        ur = await connection.execute(text("PRAGMA table_info(users)"))
        ucols = {row[1] for row in ur.fetchall()}
    except Exception:
        ucols = set()
    if ucols and "email" not in ucols:
        await connection.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users(email)"))
    if ucols and "web_pin_hash" not in ucols:
        await connection.execute(text("ALTER TABLE users ADD COLUMN web_pin_hash VARCHAR(64)"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_web_pin_hash ON users(web_pin_hash)"))
    if ucols and "student_ticket" not in ucols:
        await connection.execute(text("ALTER TABLE users ADD COLUMN student_ticket VARCHAR(64)"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_student_ticket ON users(student_ticket)"))
