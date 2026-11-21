# app/database/models.py

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base
from app.services.time_service import local_now


class AppliedMaster(Base):
    __tablename__ = "applied_sa_masters"
    version: Mapped[str] = mapped_column(String(32), primary_key=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(default=True)


class MonitoringPreferences(Base):
    __tablename__ = "monitoring_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    alerts_enabled: Mapped[bool] = mapped_column(default=True)
    controller_alerts_enabled: Mapped[bool] = mapped_column(default=True)
    controller_alert_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=-1004464440125)
    bot_paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    bot_pause_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)


class TelegramGroup(Base):
    __tablename__ = "telegram_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    group_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, onupdate=local_now)


class SAUser(Base):
    __tablename__ = "sa_users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", "group_id", name="uq_sa_user_group"),
    )

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    name: Mapped[str] = mapped_column("Name", String(255), nullable=False)
    username: Mapped[str | None] = mapped_column("Username", String(255), nullable=True)
    # Official workbook identity is kept separately from the live Telegram profile.
    official_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    official_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dealer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    position: Mapped[str | None] = mapped_column(String(50), nullable=True)
    flow_key: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    working_time: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Controller/admin override set by /change working time. Survives /sync_sa_list.
    working_time_override: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_from_list: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # excel = synchronized from the official workbook; manual = added by /add.
    source_type: Mapped[str] = mapped_column(String(20), default="excel", nullable=False, index=True)

    # /AL can pause a full day, morning only, or afternoon only.
    alert_pause_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)
    alert_paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)
    alert_pause_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Newly added users start monitoring on the next local day.
    alert_active_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)

    lat: Mapped[float | None] = mapped_column("lat", Float, nullable=True)
    lon: Mapped[float | None] = mapped_column("lon", Float, nullable=True)
    map_link: Mapped[str | None] = mapped_column("Map_Link", String(500), nullable=True)
    last_pin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, onupdate=local_now)


class SAList(Base):
    """Editable operational source for the complete official SA list."""

    __tablename__ = "sa_list"
    __table_args__ = (
        UniqueConstraint("group_name", "name", name="uq_sa_list_group_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employee_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    region: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    dealer: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    position: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    working_time: Mapped[str] = mapped_column(String(50), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    group_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    controller: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_type: Mapped[str] = mapped_column(String(20), default="excel", nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dashboard_override: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_deleted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, onupdate=local_now)


class RegionalController(Base):
    """Persistent region controller override managed by /change controller."""

    __tablename__ = "regional_controllers"

    region: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    changed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, onupdate=local_now)


class ControllerGroupAssignment(Base):
    """Persistent controller access for an exact Region-Dealer scope."""
    __tablename__ = "controller_group_assignments"
    __table_args__ = (UniqueConstraint("user_id", "group_code", name="uq_controller_user_group_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    group_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    changed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, onupdate=local_now)


class SAAlertExclusion(Base):
    """Permanent group-level exclusion created by /remove.

    The official Excel list remains the source of truth, but this record prevents
    automatic /sync_sa_list and scheduled jobs from re-adding a removed SA.
    """

    __tablename__ = "sa_alert_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "normalized_name",
            "normalized_username",
            name="uq_sa_alert_exclusion_member",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    normalized_username: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    removed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    removed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    removed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, index=True)


class PinHistory(Base):
    __tablename__ = "pin_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Canonical SA row identity. This is the primary key used by the alert
    # matcher so Telegram name/username changes cannot split a form and pin.
    sa_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    map_link: Mapped[str] = mapped_column(String(500), nullable=False)

    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, index=True)
    # Telegram event time for route chronology. pinned_at remains the bot's receipt time.
    event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outlet_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_employee_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dealer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    province: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True)
    commune: Mapped[str | None] = mapped_column(String(120), nullable=True)
    geo_checked: Mapped[bool | None] = mapped_column(nullable=True)
    leg_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)


class DealerLocation(Base):
    __tablename__ = "dealer_locations"
    region: Mapped[str] = mapped_column(String(50), primary_key=True)
    dealer: Mapped[str] = mapped_column(String(50), primary_key=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)


class SalePhotoReport(Base):
    __tablename__ = "sale_photo_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Canonical SA row identity. New reports always write this value.
    sa_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_group_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    reply_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    reply_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    reply_map_link: Mapped[str | None] = mapped_column(String(500), nullable=True)

    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replied_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)


class MissingSalePhotoAlert(Base):
    __tablename__ = "missing_sale_photo_alerts"
    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            "group_id",
            "window_start",
            "window_end",
            name="uq_missing_sale_photo_user_window",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    region: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    dealer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    position: Mapped[str | None] = mapped_column(String(50), nullable=True)
    missed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)

    # Telegram alert/reason tracking. SA users reply directly below the bot alert.
    alert_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True, index=True)
    reason_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    voice_to_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class VoiceReasonHistory(Base):
    """Persistent Telegram voice metadata and transcription audit trail."""

    __tablename__ = "voice_reason_history"
    __table_args__ = (
        UniqueConstraint("group_id", "voice_message_id", name="uq_voice_history_group_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    missing_alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alert_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    voice_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    voice_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    voice_file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, index=True)
    transcribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class PublicHoliday(Base):
    """Editable Cambodia public holidays used to auto-skip Miss Report alerts."""

    __tablename__ = "public_holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    holiday_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=local_now, onupdate=local_now)
