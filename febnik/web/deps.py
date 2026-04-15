from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from febnik.config import get_settings
from febnik.db.session import async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


def panel_base_url() -> str:
    s = get_settings()
    if s.web_public_base_url.strip():
        return s.web_public_base_url.rstrip("/")
    if s.web_host in ("0.0.0.0", "::"):
        return f"http://127.0.0.1:{s.web_port}"
    return f"http://{s.web_host}:{s.web_port}"
