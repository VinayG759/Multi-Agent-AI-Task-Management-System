from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./tasks.db"
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    timezone: str = "Asia/Kolkata"
    # When set, a new person must send "/start <code>" to use the Telegram bot. Empty means anyone can join.
    team_join_code: str = ""
    # When the password is set, the dashboard and API require signing in with this username and password.
    # Set the password whenever the app is on a public address.
    dashboard_username: str = "admin"
    dashboard_password: str = ""
    # Load the demo team and tasks at startup if the database is empty. For hosts that wipe local files.
    seed_demo_data: bool = False

    # One model per agent role, so a cheaper or faster model can be swapped in from .env.
    orchestrator_model: str = "claude-opus-5-5"
    specialist_model: str = "claude-opus-5-5"
    reviewer_model: str = "claude-opus-5-5"
    # How hard the model thinks: low | medium | high. Lower is faster and cheaper.
    llm_effort: str = "low"
    # Let Anthropic re-run a request on another model if a safety filter declines it.
    llm_fallbacks: bool = True

    enable_telegram: bool = True
    enable_scheduler: bool = True

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
