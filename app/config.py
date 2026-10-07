from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./tasks.db"
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    timezone: str = "Asia/Kolkata"

    orchestrator_model: str = "claude-sonnet-5-5"
    specialist_model: str = "claude-sonnet-5-5"
    reviewer_model: str = "claude-haiku-4-5-20251001"

    enable_telegram: bool = True
    enable_scheduler: bool = True

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
