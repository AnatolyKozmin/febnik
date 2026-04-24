"""Лёгкие правки схемы SQLite для существующих БД (без Alembic)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def _migrate_feedback_responses_to_json(connection: AsyncConnection) -> None:
    r = await connection.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_responses'")
    )
    if r.fetchone() is None:
        return
    info = await connection.execute(text("PRAGMA table_info(feedback_responses)"))
    cols = {row[1] for row in info.fetchall()}
    if "answers_json" in cols:
        return
    if "answer_liked" not in cols:
        await connection.execute(text("ALTER TABLE feedback_responses ADD COLUMN answers_json TEXT NOT NULL DEFAULT '{}'"))
        return
    rows = (
        await connection.execute(
            text(
                "SELECT id, user_id, day, answer_liked, answer_improve, answer_extra, created_at "
                "FROM feedback_responses"
            )
        )
    ).fetchall()
    await connection.execute(text("DROP TABLE feedback_responses"))
    await connection.execute(
        text(
            """
            CREATE TABLE feedback_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day INTEGER NOT NULL,
                answers_json TEXT NOT NULL,
                created_at DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE (user_id, day)
            )
            """
        )
    )
    for row in rows:
        payload = {
            "_legacy_v1": {
                "answer_liked": row[3],
                "answer_improve": row[4],
                "answer_extra": row[5],
            }
        }
        await connection.execute(
            text(
                "INSERT INTO feedback_responses (id, user_id, day, answers_json, created_at) "
                "VALUES (:id, :uid, :day, :j, :ca)"
            ),
            {
                "id": row[0],
                "uid": row[1],
                "day": row[2],
                "j": json.dumps(payload, ensure_ascii=False),
                "ca": row[6],
            },
        )
    await connection.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_user_day ON feedback_responses(user_id, day)")
    )


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

    await _migrate_feedback_responses_to_json(connection)

    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS scan_award_idempotency (
                idempotency_key VARCHAR(64) NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount_feb INTEGER NOT NULL,
                transaction_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
            )
            """
        )
    )
    await connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_scan_award_idempotency_user_id "
            "ON scan_award_idempotency(user_id)"
        )
    )
