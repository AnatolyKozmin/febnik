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

    # Заявка на ФЭБарт с сайта (/cabinet/request). Заявки из бота (/request) не зависят от этого.
    web_balance_request_enabled: bool = False
    # Заявка /request (бот): максимум ФЭБарт за одну заявку
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

    # Вход участника по почте: код в письме. Если SMTP_HOST пуст — код пишется в лог сервера.
    # Gmail: smtp.gmail.com:587, SMTP_STARTTLS=true, SMTP_USER=полный адрес, SMTP_PASSWORD=пароль приложения (не пароль аккаунта).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    # Gmail: альтернатива 587+STARTTLS — порт 465 с TLS сразу (иногда на VPS 587 «висит», 465 проходит).
    smtp_implicit_ssl: bool = False
    # На VPS часто сломан исходящий IPv6 — Gmail тогда «висит». Включите на сервере.
    smtp_prefer_ipv4: bool = False
    join_otp_ttl_seconds: int = 600
    join_otp_resend_seconds: int = 60
    # «Запомнить» веб-участника после входа по почте (подписанная cookie febnik_p).
    participant_token_max_age_seconds: int = 10 * 365 * 24 * 3600
    # В проде за HTTPS включите true — cookie только по TLS.
    web_cookie_secure: bool = False
    # Каталог для плашек кабинета (день 1–3). В Docker задайте, например, /data/cabinet_banners
    cabinet_banners_dir: str = "uploads/cabinet_banners"

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
