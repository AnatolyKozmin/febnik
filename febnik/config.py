from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # При BOT_ENABLED=false бот не запускается — только веб (участники и админка).
    bot_enabled: bool = False
    bot_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./febnik.db"

    # HTTP-клиент Telegram (aiohttp): таймаут и опционально прокси (если API недоступен из сети)
    telegram_request_timeout: float = 120.0
    telegram_proxy: str | None = None

    # Заявка на ФЭБ с сайта (/cabinet/request). Заявки из бота (/request) не зависят от этого.
    web_balance_request_enabled: bool = False
    # Заявка /request (бот): максимум ФЭБ за одну заявку
    max_balance_request_feb: int = 5000
    # Начисление по QR (интерактив): верхняя граница суммы за одно действие
    max_qr_award_feb: int = 10000

    google_credentials_path: str | None = None
    google_spreadsheet_id: str | None = None
    sheet_activities: str = "Интерактивы"
    sheet_prizes: str = "Призы"
    sheet_log: str = "Лог_операций"

    org_telegram_ids: str = ""
    handout_telegram_ids: str = ""

    web_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_public_base_url: str = ""

    admin_username: str = "admin"
    admin_password: str = "change-me"
    session_secret: str = "change-me-generate-long-random-string"

    @model_validator(mode="after")
    def _require_bot_token_if_bot(self) -> "Settings":
        if self.bot_enabled and not (self.bot_token or "").strip():
            raise ValueError("Задайте BOT_TOKEN в .env, если BOT_ENABLED=true")
        return self

    @staticmethod
    def _parse_ids(raw: str) -> set[int]:
        if not raw.strip():
            return set()
        out: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            out.add(int(part))
        return out

    @property
    def org_ids(self) -> set[int]:
        return self._parse_ids(self.org_telegram_ids)

    @property
    def handout_ids(self) -> set[int]:
        h = self._parse_ids(self.handout_telegram_ids)
        if h:
            return h
        return self.org_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_org(telegram_id: int) -> bool:
    return telegram_id in get_settings().org_ids


def can_handout(telegram_id: int) -> bool:
    return telegram_id in get_settings().handout_ids
