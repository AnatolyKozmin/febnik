from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./febnik.db"

    # HTTP-клиент Telegram (aiohttp): таймаут и опционально прокси (если API недоступен из сети)
    telegram_request_timeout: float = 120.0
    telegram_proxy: str | None = None

    # Заявка /request: максимум ФЭБ за одну заявку
    max_balance_request_feb: int = 5000

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
