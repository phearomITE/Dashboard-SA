# app/core/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    DB_HOST: str = "localhost"
    DB_PORT: int = 5437
    DB_NAME: str = "alert_message"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DB_SSLMODE: str = ""

    TELEGRAM_BOT_TOKEN: str
    DASHBOARD_ENABLED: bool = True
    DASHBOARD_AUTH_REQUIRED: bool = True
    DASHBOARD_HOST: str = "127.0.0.1"
    DASHBOARD_PORT: int = 8080
    PORT: int = 0
    APP_TIMEZONE: str = "Asia/Phnom_Penh"
    LOG_LEVEL: str = "INFO"
    # Leave empty in Docker so logs go to stdout and are managed by Docker.
    # A Windows deployment may set this to logs/bot.log for rotating file logs.
    LOG_FILE: str = ""
    LOG_MAX_BYTES: int = 10_000_000
    LOG_BACKUP_COUNT: int = 5

    WORK_DAYS: str = "0,1,2,3,4"
    REMINDER_DAYS: str = "mon-fri"

    WORK_START_HOUR: int = 8
    WORK_START_MINUTE: int = 20
    MORNING_END_HOUR: int = 11
    MORNING_END_MINUTE: int = 47

    BREAK_START_HOUR: int = 12
    BREAK_START_MINUTE: int = 0
    BREAK_END_HOUR: int = 13
    BREAK_END_MINUTE: int = 0

    AFTERNOON_START_HOUR: int = 13
    AFTERNOON_START_MINUTE: int = 20
    WORK_END_HOUR: int = 16
    WORK_END_MINUTE: int = 24

    ALERT_INTERVAL_MINUTES: int = 20
    # Scheduled checks fire at :59 and wait this many seconds so Telegram
    # updates from the final displayed minute can reach PostgreSQL first.
    ALERT_SETTLE_SECONDS: float = 2.0
    SEND_ALL_PINNED_MESSAGE: bool = False

    FIRST_PHOTO_CHECK_START_HOUR: int = 6
    FIRST_PHOTO_CHECK_START_MINUTE: int = 0
    FIRST_PHOTO_CHECK_END_HOUR: int = 8
    FIRST_PHOTO_CHECK_END_MINUTE: int = 20
    FIRST_PHOTO_MIN_SUBMISSIONS: int = 2
    FIRST_PHOTO_MESSAGE: str = "បងៗម៉េចបានអត់ទាន់ឃើញទម្លាក់រូបភាពលក់ដំបូងចឹងបងៗ🙏"

    MIDDAY_CHECK_START_HOUR: int = 11
    MIDDAY_CHECK_START_MINUTE: int = 24
    MIDDAY_CHECK_END_HOUR: int = 11
    MIDDAY_CHECK_END_MINUTE: int = 47
    MIDDAY_MIN_SUBMISSIONS: int = 2
    MIDDAY_MESSAGE: str = "បងៗម៉េចបានអត់ទាន់ឃើញទម្លាក់រូបភាពលក់ចឹងបងៗ🙏"

    AFTERNOON_CHECK_START_HOUR: int = 13
    AFTERNOON_CHECK_START_MINUTE: int = 0
    AFTERNOON_CHECK_END_HOUR: int = 13
    AFTERNOON_CHECK_END_MINUTE: int = 20
    AFTERNOON_MIN_SUBMISSIONS: int = 2
    AFTERNOON_MESSAGE: str = "បងមិចបានអត់ទានចូលលក់🙏"

    LAST_PHOTO_CHECK_START_HOUR: int = 16
    LAST_PHOTO_CHECK_START_MINUTE: int = 1
    LAST_PHOTO_CHECK_END_HOUR: int = 16
    LAST_PHOTO_CHECK_END_MINUTE: int = 24
    LAST_PHOTO_MIN_SUBMISSIONS: int = 2
    LAST_PHOTO_MESSAGE: str = "បងៗម៉េចបានអត់ទាន់ឃើញទម្លាក់រូបភាពលក់ចុងក្រោយចឹងបងៗ🙏"

    CONTROLLER_NAME: str = ""
    CONTROLLER_USERNAME: str = ""
    CONTROLLER_USER_ID: int = 0

    TELETHON_API_ID: int | None = None
    TELETHON_API_HASH: str = ""
    TELETHON_STRING_SESSION: str = ""
    TELETHON_SESSION_FILE: str = "backups/telethon_recovery.session"

    # Ignore legacy .env keys from earlier releases and accept UTF-8 files
    # safely after Windows PowerShell rewrites them.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        raw = self.DATABASE_URL.strip()
        if raw:
            if raw.startswith("postgres://"):
                return "postgresql+psycopg2://" + raw[len("postgres://"):]
            if raw.startswith("postgresql://"):
                return "postgresql+psycopg2://" + raw[len("postgresql://"):]
            return raw
        url = f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        if self.DB_SSLMODE.strip():
            return f"{url}?sslmode={self.DB_SSLMODE.strip()}"
        return url

    @property
    def work_days_list(self) -> list[int]:
        return [int(x.strip()) for x in self.WORK_DAYS.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
