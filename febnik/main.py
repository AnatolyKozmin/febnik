import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from febnik.bot.commands_setup import setup_bot_commands
from febnik.bot.handlers import balance_request, claim, staff, start, user
from febnik.bot.middlewares import DbSessionMiddleware, StaffCommandsMiddleware
from febnik.config import get_settings
from febnik.db.session import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    settings = get_settings()

    tg_session = AiohttpSession(
        timeout=settings.telegram_request_timeout,
        proxy=settings.telegram_proxy or None,
    )
    bot = Bot(settings.bot_token, session=tg_session)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(DbSessionMiddleware())
    _staff_mw = StaffCommandsMiddleware()
    dp.message.middleware(_staff_mw)
    dp.callback_query.middleware(_staff_mw)
    dp.include_router(start.router)
    dp.include_router(user.router)
    dp.include_router(balance_request.router)
    dp.include_router(claim.router)
    dp.include_router(staff.router)

    await setup_bot_commands(bot)

    try:
        if settings.web_enabled:
            import uvicorn

            from febnik.web.app import create_app

            app = create_app()
            # asyncio вместо uvloop: меньше конфликтов с aiohttp/aiogram в одном процессе
            cfg = uvicorn.Config(
                app,
                host=settings.web_host,
                port=settings.web_port,
                log_level="info",
                loop="asyncio",
            )
            server = uvicorn.Server(cfg)
            logger.info(
                "Веб-панель: http://%s:%s (для ссылок в боте задайте WEB_PUBLIC_BASE_URL, если хост не localhost)",
                settings.web_host,
                settings.web_port,
            )
            await asyncio.gather(server.serve(), dp.start_polling(bot))
        else:
            await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
