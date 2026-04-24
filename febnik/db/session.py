import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from febnik.db.base import Base
import febnik.db.models  # noqa: F401 — регистрация таблиц в metadata

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./febnik.db")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from febnik.db.sqlite_migrate import apply_sqlite_migrations
    from febnik.db.models import WebAppState

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        await apply_sqlite_migrations(conn)
    from febnik.db.models import FeedbackSurveySlot

    async with async_session_factory() as session:
        changed = False
        if await session.get(WebAppState, 1) is None:
            session.add(WebAppState(id=1, cabinet_banner_active_day=None))
            changed = True
        for d in (1, 2, 3):
            if await session.get(FeedbackSurveySlot, d) is None:
                session.add(FeedbackSurveySlot(day=d, is_open=False, reward_feb=0, title=None))
                changed = True
        if changed:
            await session.commit()


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
