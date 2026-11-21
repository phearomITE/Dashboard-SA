

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import get_settings

settings = get_settings()
_connect_args = (
    {
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    if settings.database_url.startswith("postgresql")
    else {}
)
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    connect_args=_connect_args,
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Keep the PostgreSQL session open for the lifetime of the bot.  A session-level
# advisory lock prevents two copies of this project from running schedulers
# against the same database while Telegram updates are consumed by only one of
# them (the most common cause of widespread false missing alerts).
_RUNTIME_LOCK_KEY = 731_540_051_425_620
_runtime_lock_connection = None


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from app.database import models  # noqa
    Base.metadata.create_all(bind=engine)


def acquire_runtime_lock() -> bool:
    """Return False when another bot instance already owns this database."""
    global _runtime_lock_connection
    if engine.dialect.name != "postgresql" or settings.DATABASE_URL.strip():
        return True
    if _runtime_lock_connection is not None:
        return True
    connection = engine.connect()
    acquired = bool(
        connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": _RUNTIME_LOCK_KEY},
        ).scalar()
    )
    if not acquired:
        connection.close()
        return False
    _runtime_lock_connection = connection
    return True


def release_runtime_lock() -> None:
    global _runtime_lock_connection
    connection = _runtime_lock_connection
    _runtime_lock_connection = None
    if connection is None:
        return
    try:
        connection.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _RUNTIME_LOCK_KEY},
        )
    finally:
        connection.close()
