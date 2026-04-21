"""Пути к ресурсам рядом с кодом `febnik/web` (надёжно в Docker и без find_spec)."""

from pathlib import Path

_WEB_ROOT = Path(__file__).resolve().parent


def join_logo_svg_path() -> Path:
    """Запасной SVG (подставляется в шаблоне при ошибке загрузки PNG)."""
    return _WEB_ROOT / "templates" / "participant" / "includes" / "logo-registration.svg"
