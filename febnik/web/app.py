import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from febnik.config import get_settings
from febnik.services.cabinet_banners import ensure_banners_dir
from febnik.web.routes_admin import router as admin_router
from febnik.web.routes_participant import router as participant_router
from febnik.web.routes_public import router as public_router
from febnik.web.routes_scan import router as scan_router

logger = logging.getLogger(__name__)


class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        p = request.url.path
        if p.startswith("/admin/login"):
            return await call_next(request)
        if p.startswith("/admin"):
            if not request.session.get("admin"):
                return RedirectResponse(url="/admin/login", status_code=302)
        return await call_next(request)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ФЭБник", docs_url=None, redoc_url=None)

    # Порядок: последний добавленный — внешний (первым обрабатывает запрос).
    app.add_middleware(AdminAuthMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=False,
        max_age=30 * 24 * 3600,
        same_site="lax",
    )

    app.include_router(public_router)
    app.include_router(participant_router)
    app.include_router(scan_router)
    app.include_router(admin_router)

    _static = Path(__file__).resolve().parent / "static"
    if _static.is_dir():
        app.mount("/static", StaticFiles(directory=str(_static)), name="static")
    _banner_root = ensure_banners_dir(settings)
    app.mount(
        "/media/cabinet-banners",
        StaticFiles(directory=str(_banner_root)),
        name="cabinet_banners",
    )
    return app
