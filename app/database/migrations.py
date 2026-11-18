# app/database/migrations.py

from sqlalchemy import inspect, text

from app.database.database import engine


def _columns(conn, table_name: str) -> list[str]:
    # Inspect through the migration transaction. A separate connection can
    # block on uncommitted ALTER TABLE statements from this same transaction.
    inspector = inspect(conn)
    if table_name not in inspector.get_table_names():
        return []
    return [c["name"] for c in inspector.get_columns(table_name)]


def add_missing_columns() -> None:
    try:
        with engine.connect() as chk:
            done = chk.execute(
                text("SELECT 1 FROM applied_sa_masters WHERE version = 'schema-2026-11-17'")
            ).scalar()
            if done == 1:
                return
    except Exception:
        pass

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL lock_timeout = '5000ms'"))
        if "monitoring_preferences" in tables:
            preferences_columns = _columns(conn, "monitoring_preferences")
            if "controller_alerts_enabled" not in preferences_columns:
                conn.execute(text("ALTER TABLE monitoring_preferences ADD COLUMN controller_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE"))
            if "controller_alert_group_id" not in preferences_columns:
                conn.execute(text("ALTER TABLE monitoring_preferences ADD COLUMN controller_alert_group_id BIGINT DEFAULT -1004464440125"))
            if "bot_paused_until" not in preferences_columns:
                conn.execute(text("ALTER TABLE monitoring_preferences ADD COLUMN bot_paused_until TIMESTAMP WITHOUT TIME ZONE"))
            if "bot_pause_reason" not in preferences_columns:
                conn.execute(text("ALTER TABLE monitoring_preferences ADD COLUMN bot_pause_reason VARCHAR(100)"))
        if "telegram_groups" not in tables:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS telegram_groups (
                    id SERIAL PRIMARY KEY,
                    group_id BIGINT UNIQUE NOT NULL,
                    group_name VARCHAR(255),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
            """))

        if "sa_users" in tables:
            cols = _columns(conn, "sa_users")
            if "group_id" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN group_id BIGINT NOT NULL DEFAULT 0"))
            if "group_name" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN group_name VARCHAR(255)"))
            if "Map_Link" not in cols:
                conn.execute(text('ALTER TABLE sa_users ADD COLUMN "Map_Link" VARCHAR(500)'))
            if "region" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN region VARCHAR(50)"))
            if "dealer" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN dealer VARCHAR(50)"))
            if "position" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN position VARCHAR(50)"))
            if "flow_key" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN flow_key VARCHAR(50)"))
            if "working_time" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN working_time VARCHAR(50)"))
            if "working_time_override" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN working_time_override VARCHAR(50)"))
            if "is_from_list" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN is_from_list INTEGER NOT NULL DEFAULT 0"))
            if "source_type" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN source_type VARCHAR(20) NOT NULL DEFAULT 'excel'"))
            if "official_name" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN official_name VARCHAR(255)"))
            if "official_username" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN official_username VARCHAR(255)"))
            conn.execute(text('UPDATE sa_users SET official_name = "Name" WHERE official_name IS NULL'))
            conn.execute(text('UPDATE sa_users SET official_username = "Username" WHERE official_username IS NULL'))
            if "alert_pause_start" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN alert_pause_start TIMESTAMP WITHOUT TIME ZONE"))
            if "alert_paused_until" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN alert_paused_until TIMESTAMP WITHOUT TIME ZONE"))
            if "alert_pause_reason" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN alert_pause_reason VARCHAR(50)"))
            if "alert_active_from" not in cols:
                conn.execute(text("ALTER TABLE sa_users ADD COLUMN alert_active_from TIMESTAMP WITHOUT TIME ZONE"))

            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_users_flow_key ON sa_users (flow_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_users_source_type ON sa_users (source_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_users_alert_pause_start ON sa_users (alert_pause_start)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_users_alert_paused_until ON sa_users (alert_paused_until)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_users_alert_active_from ON sa_users (alert_active_from)"))

            conn.execute(text("""
                UPDATE sa_users
                SET "Map_Link" = CONCAT(
                    'https://maps.google.com/maps?q=', lat, ',', lon,
                    '&ll=', lat, ',', lon, '&z=16'
                )
                WHERE lat IS NOT NULL AND lon IS NOT NULL AND "Map_Link" IS NULL
            """))

            conn.execute(text("""
                DO $$
                DECLARE constraint_name text;
                BEGIN
                    SELECT con.conname INTO constraint_name
                    FROM pg_constraint con
                    JOIN pg_class rel ON rel.oid = con.conrelid
                    JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey)
                    WHERE rel.relname = 'sa_users'
                      AND con.contype = 'u'
                      AND att.attname = 'telegram_user_id'
                    LIMIT 1;
                    IF constraint_name IS NOT NULL
                       AND constraint_name <> 'uq_sa_user_group' THEN
                        EXECUTE 'ALTER TABLE sa_users DROP CONSTRAINT ' || quote_ident(constraint_name);
                    END IF;
                END $$;
            """))

            # Old releases could leave two rows for the same Telegram account
            # in one group. Repoint activity to one canonical official row and
            # remove duplicates before enforcing the unique identity index.
            conn.execute(text("""
                WITH ranked AS (
                    SELECT "Id",
                           FIRST_VALUE("Id") OVER (
                               PARTITION BY telegram_user_id, group_id
                               ORDER BY is_from_list DESC,
                                        CASE WHEN region IS NOT NULL AND dealer IS NOT NULL
                                                  AND position IS NOT NULL THEN 1 ELSE 0 END DESC,
                                        "Id" ASC
                           ) AS keep_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY telegram_user_id, group_id
                               ORDER BY is_from_list DESC,
                                        CASE WHEN region IS NOT NULL AND dealer IS NOT NULL
                                                  AND position IS NOT NULL THEN 1 ELSE 0 END DESC,
                                        "Id" ASC
                           ) AS rn
                    FROM sa_users
                    WHERE telegram_user_id > 0
                )
                UPDATE pin_history p
                SET sa_user_id = ranked.keep_id
                FROM ranked
                WHERE ranked.rn > 1 AND p.sa_user_id = ranked."Id"
            """)) if "pin_history" in tables else None
            conn.execute(text("""
                WITH ranked AS (
                    SELECT "Id",
                           FIRST_VALUE("Id") OVER (
                               PARTITION BY telegram_user_id, group_id
                               ORDER BY is_from_list DESC,
                                        CASE WHEN region IS NOT NULL AND dealer IS NOT NULL
                                                  AND position IS NOT NULL THEN 1 ELSE 0 END DESC,
                                        "Id" ASC
                           ) AS keep_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY telegram_user_id, group_id
                               ORDER BY is_from_list DESC,
                                        CASE WHEN region IS NOT NULL AND dealer IS NOT NULL
                                                  AND position IS NOT NULL THEN 1 ELSE 0 END DESC,
                                        "Id" ASC
                           ) AS rn
                    FROM sa_users
                    WHERE telegram_user_id > 0
                )
                UPDATE sale_photo_reports r
                SET sa_user_id = ranked.keep_id
                FROM ranked
                WHERE ranked.rn > 1 AND r.sa_user_id = ranked."Id"
            """)) if "sale_photo_reports" in tables else None
            conn.execute(text("""
                DELETE FROM sa_users victim
                USING sa_users keeper
                WHERE victim.telegram_user_id > 0
                  AND victim.telegram_user_id = keeper.telegram_user_id
                  AND victim.group_id = keeper.group_id
                  AND victim."Id" <> keeper."Id"
                  AND (
                      keeper.is_from_list > victim.is_from_list
                      OR (keeper.is_from_list = victim.is_from_list AND keeper."Id" < victim."Id")
                  )
            """))

            conn.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_sa_user_group_idx
                ON sa_users (telegram_user_id, group_id)
            """))

        if "pin_history" in tables:
            pin_columns = _columns(conn, "pin_history")
            additions = {"event_at": "TIMESTAMP", "telegram_update_id": "BIGINT", "outlet_name": "TEXT", "reported_employee_id": "TEXT",
                         "region": "VARCHAR(50)", "dealer": "VARCHAR(50)",
                         "province": "VARCHAR(120)", "district": "VARCHAR(120)", "commune": "VARCHAR(120)",
                         "geo_checked": "BOOLEAN", "leg_km": "DOUBLE PRECISION"}
            for name, sql_type in additions.items():
                if name not in pin_columns:
                    conn.execute(text(f"ALTER TABLE pin_history ADD COLUMN {name} {sql_type}"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_pin_update_id ON pin_history(telegram_update_id) WHERE telegram_update_id IS NOT NULL"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pin_route_day ON pin_history(COALESCE(event_at, pinned_at), telegram_user_id)"))

        if "sale_photo_reports" in tables:
            conn.execute(text("ALTER TABLE sale_photo_reports ADD COLUMN IF NOT EXISTS media_group_id VARCHAR(128)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sale_photo_reports_media_group_id ON sale_photo_reports(media_group_id)"))

        if "sa_list" in tables:
            for name, sql_type in {"employee_id": "VARCHAR(64)",
                                   "source_key": "VARCHAR(500)",
                                   "dashboard_override": "INTEGER NOT NULL DEFAULT 0",
                                   "is_deleted": "INTEGER NOT NULL DEFAULT 0"}.items():
                if name not in _columns(conn, "sa_list"):
                    conn.execute(text(f"ALTER TABLE sa_list ADD COLUMN {name} {sql_type}"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_list_employee_id ON sa_list(employee_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_list_source_key ON sa_list(source_key)"))

        for table_name in ["pin_history", "sale_photo_reports"]:
            if table_name in tables:
                cols = _columns(conn, table_name)
                if "group_id" not in cols:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN group_id BIGINT NOT NULL DEFAULT 0"))
                if "group_name" not in cols:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN group_name VARCHAR(255)"))
                # Canonical SA identity. New submissions write this directly;
                # existing history is backfilled from stable Telegram user ID.
                if "sa_user_id" not in cols:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN sa_user_id INTEGER"))
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_sa_user_id ON {table_name} (sa_user_id)"))

        # Backfill canonical identity for historical rows. This repairs today's
        # already-recorded form/pin history after upgrading, when a stable
        # Telegram ID is available in sa_users. Use a correlated subquery with
        # LIMIT 1 so old duplicate rows cannot break migration startup.
        if "pin_history" in tables and "sa_users" in tables:
            conn.execute(text("""
                UPDATE pin_history p
                SET sa_user_id = (
                    SELECT u."Id"
                    FROM sa_users u
                    WHERE u.group_id = p.group_id
                      AND u.telegram_user_id = p.telegram_user_id
                      AND u.is_from_list = 1
                    ORDER BY u."Id"
                    LIMIT 1
                )
                WHERE (p.sa_user_id IS NULL OR NOT EXISTS (SELECT 1 FROM sa_users x WHERE x."Id" = p.sa_user_id))
                  AND EXISTS (
                    SELECT 1 FROM sa_users u
                    WHERE u.group_id = p.group_id
                      AND u.telegram_user_id = p.telegram_user_id
                      AND u.is_from_list = 1
                  )
            """))
        if "sale_photo_reports" in tables and "sa_users" in tables:
            conn.execute(text("""
                UPDATE sale_photo_reports r
                SET sa_user_id = (
                    SELECT u."Id"
                    FROM sa_users u
                    WHERE u.group_id = r.group_id
                      AND u.telegram_user_id = r.telegram_user_id
                      AND u.is_from_list = 1
                    ORDER BY u."Id"
                    LIMIT 1
                )
                WHERE (r.sa_user_id IS NULL OR NOT EXISTS (SELECT 1 FROM sa_users x WHERE x."Id" = r.sa_user_id))
                  AND EXISTS (
                    SELECT 1 FROM sa_users u
                    WHERE u.group_id = r.group_id
                      AND u.telegram_user_id = r.telegram_user_id
                      AND u.is_from_list = 1
                  )
            """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS missing_sale_photo_alerts (
                id SERIAL PRIMARY KEY,
                telegram_user_id BIGINT NOT NULL,
                group_id BIGINT NOT NULL,
                group_name VARCHAR(255),
                name VARCHAR(255) NOT NULL,
                username VARCHAR(255),
                region VARCHAR(50),
                dealer VARCHAR(50),
                position VARCHAR(50),
                missed_at TIMESTAMP WITHOUT TIME ZONE,
                window_start TIMESTAMP WITHOUT TIME ZONE,
                window_end TIMESTAMP WITHOUT TIME ZONE,
                CONSTRAINT uq_missing_sale_photo_user_window
                    UNIQUE (telegram_user_id, group_id, window_start, window_end)
            )
        """))

        missing_cols = _columns(conn, "missing_sale_photo_alerts")
        if "region" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN region VARCHAR(50)"))
        if "dealer" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN dealer VARCHAR(50)"))
        if "position" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN position VARCHAR(50)"))
        if "alert_message_id" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN alert_message_id BIGINT"))
        if "reason_text" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN reason_text TEXT"))
        if "reason_submitted_at" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN reason_submitted_at TIMESTAMP WITHOUT TIME ZONE"))
        if "reason_message_id" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN reason_message_id BIGINT"))
        if "voice_to_text" not in missing_cols:
            conn.execute(text("ALTER TABLE missing_sale_photo_alerts ADD COLUMN voice_to_text TEXT"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_missing_sale_photo_alerts_region ON missing_sale_photo_alerts (region)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_missing_alert_message_id ON missing_sale_photo_alerts (group_id, alert_message_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_missing_reason_submitted_at ON missing_sale_photo_alerts (reason_submitted_at)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS voice_reason_history (
                id SERIAL PRIMARY KEY,
                missing_alert_id INTEGER,
                telegram_user_id BIGINT NOT NULL,
                group_id BIGINT NOT NULL,
                group_name VARCHAR(255),
                sender_name VARCHAR(255) NOT NULL,
                sender_username VARCHAR(255),
                alert_message_id BIGINT,
                voice_message_id BIGINT NOT NULL,
                voice_file_id TEXT NOT NULL,
                voice_file_unique_id VARCHAR(255),
                duration_seconds INTEGER,
                file_size_bytes INTEGER,
                status VARCHAR(30) NOT NULL DEFAULT 'pending',
                transcript TEXT,
                error_text TEXT,
                received_at TIMESTAMP WITHOUT TIME ZONE,
                transcribed_at TIMESTAMP WITHOUT TIME ZONE,
                CONSTRAINT uq_voice_history_group_message UNIQUE (group_id, voice_message_id)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_voice_history_user ON voice_reason_history (telegram_user_id, group_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_voice_history_status ON voice_reason_history (status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_voice_history_alert ON voice_reason_history (missing_alert_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_voice_history_received ON voice_reason_history (received_at)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sa_alert_exclusions (
                id SERIAL PRIMARY KEY,
                group_id BIGINT NOT NULL,
                group_name VARCHAR(255),
                name VARCHAR(255) NOT NULL,
                username VARCHAR(255),
                normalized_name VARCHAR(255) NOT NULL DEFAULT '',
                normalized_username VARCHAR(255) NOT NULL DEFAULT '',
                removed_by_user_id BIGINT,
                removed_by_name VARCHAR(255),
                removed_at TIMESTAMP WITHOUT TIME ZONE,
                CONSTRAINT uq_sa_alert_exclusion_member
                    UNIQUE (group_id, normalized_name, normalized_username)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_alert_exclusions_group_id ON sa_alert_exclusions (group_id)"))
        exclusion_cols = _columns(conn, "sa_alert_exclusions")
        if "telegram_user_id" not in exclusion_cols:
            conn.execute(text("ALTER TABLE sa_alert_exclusions ADD COLUMN telegram_user_id BIGINT"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_alert_exclusions_telegram_user_id ON sa_alert_exclusions (telegram_user_id)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sa_list (
                id SERIAL PRIMARY KEY,
                region VARCHAR(50) NOT NULL,
                dealer VARCHAR(50) NOT NULL,
                name VARCHAR(255) NOT NULL,
                position VARCHAR(50) NOT NULL,
                working_time VARCHAR(50) NOT NULL,
                username VARCHAR(255),
                group_name VARCHAR(255) NOT NULL,
                controller VARCHAR(500),
                source_type VARCHAR(20) NOT NULL DEFAULT 'excel',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP WITHOUT TIME ZONE,
                updated_at TIMESTAMP WITHOUT TIME ZONE,
                CONSTRAINT uq_sa_list_group_name UNIQUE (group_name, name)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_list_region ON sa_list (region)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_list_dealer ON sa_list (dealer)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_list_group_name ON sa_list (group_name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_list_is_active ON sa_list (is_active)"))
        sa_list_cols = _columns(conn, "sa_list")
        if "telegram_user_id" not in sa_list_cols:
            conn.execute(text("ALTER TABLE sa_list ADD COLUMN telegram_user_id BIGINT"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sa_list_telegram_user_id ON sa_list (telegram_user_id)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS regional_controllers (
                region VARCHAR(10) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                username VARCHAR(255) NOT NULL,
                user_id BIGINT NOT NULL,
                changed_by_user_id BIGINT,
                updated_at TIMESTAMP WITHOUT TIME ZONE
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS controller_group_assignments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                group_code VARCHAR(50) NOT NULL,
                name VARCHAR(255) NOT NULL,
                username VARCHAR(255) NOT NULL DEFAULT '',
                changed_by_user_id BIGINT,
                created_at TIMESTAMP WITHOUT TIME ZONE,
                updated_at TIMESTAMP WITHOUT TIME ZONE,
                CONSTRAINT uq_controller_user_group_code UNIQUE (user_id, group_code)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_controller_group_user ON controller_group_assignments (user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_controller_group_code ON controller_group_assignments (group_code)"))
        if "applied_sa_masters" in tables:
            conn.execute(text("INSERT INTO applied_sa_masters (version) VALUES ('schema-2026-11-17') ON CONFLICT DO NOTHING"))
