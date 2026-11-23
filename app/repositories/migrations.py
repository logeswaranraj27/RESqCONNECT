"""Database migration runner for SQLite and PostgreSQL.
Idempotent and safe to run on startup before workers start.
"""
import logging
from app.repositories.base import utc_now

logger = logging.getLogger("resqconnect.migrations")


def add_column_if_missing(connection, is_postgres, table, column, col_type):
    cursor = connection.cursor()
    try:
        if is_postgres:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}")
        else:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [row[1] if isinstance(row, tuple) else row["name"] for row in cursor.fetchall()]
            if column not in columns:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        connection.commit()
    except Exception as exc:
        logger.debug(f"Column check for {table}.{column}: {exc}")
        connection.rollback() if is_postgres else None


def run_migrations(database):
    """Safely apply schema additions and data backfills."""
    conn = database.connect()
    is_pg = database.is_postgres
    try:
        cursor = conn.cursor()

        # 1. New tables
        new_tables_sql = [
            """CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )""",
            """CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                team_id TEXT,
                role TEXT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                incident_id TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS scheduler_locks (
                lock_name TEXT PRIMARY KEY,
                locked_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                locked_by TEXT NOT NULL
            )"""
        ]
        for stmt in new_tables_sql:
            cursor.execute(stmt)
        conn.commit()

        # 2. Add columns to users
        add_column_if_missing(conn, is_pg, "users", "email", "TEXT")
        add_column_if_missing(conn, is_pg, "users", "contact_number", "TEXT")
        add_column_if_missing(conn, is_pg, "users", "failed_login_count", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing(conn, is_pg, "users", "locked_until", "TEXT")
        add_column_if_missing(conn, is_pg, "users", "created_at", "TEXT")
        add_column_if_missing(conn, is_pg, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing(conn, is_pg, "users", "team_id", "TEXT")

        # 3. Add columns to incidents
        add_column_if_missing(conn, is_pg, "incidents", "reported_severity", "TEXT")
        add_column_if_missing(conn, is_pg, "incidents", "effective_severity", "TEXT")
        add_column_if_missing(conn, is_pg, "incidents", "duplicate_of", "TEXT")
        add_column_if_missing(conn, is_pg, "incidents", "cancellation_reason", "TEXT")
        add_column_if_missing(conn, is_pg, "incidents", "officer_notes", "TEXT")
        add_column_if_missing(conn, is_pg, "incidents", "version", "INTEGER NOT NULL DEFAULT 1")

        # 4. Add columns to assignments & rescue_teams
        add_column_if_missing(conn, is_pg, "assignments", "version", "INTEGER NOT NULL DEFAULT 1")
        add_column_if_missing(conn, is_pg, "rescue_teams", "version", "INTEGER NOT NULL DEFAULT 1")

        # 5. Data backfill
        now = utc_now()
        # Backfill users
        cursor.execute("UPDATE users SET email = LOWER(username) || '@resqconnect.org' WHERE email IS NULL OR email = ''")
        cursor.execute("UPDATE users SET failed_login_count = 0 WHERE failed_login_count IS NULL")
        cursor.execute("UPDATE users SET is_active = 1 WHERE is_active IS NULL")
        cursor.execute("UPDATE users SET must_change_password = 0 WHERE must_change_password IS NULL")
        cursor.execute(database.placeholder_sql("UPDATE users SET created_at = ? WHERE created_at IS NULL"), (now,))

        # Backfill incidents
        cursor.execute("UPDATE incidents SET reported_severity = severity WHERE reported_severity IS NULL OR reported_severity = ''")
        cursor.execute("UPDATE incidents SET effective_severity = severity WHERE effective_severity IS NULL OR effective_severity = ''")
        cursor.execute("UPDATE incidents SET version = 1 WHERE version IS NULL OR version = 0")

        # Protect user-submitted incidents: set is_demo = 0 for any non-seeded incidents (including incidentc7e917cb2126)
        demo_ids = "('incident101','incident102','incident103','incident104','incident105','incident106','incident107','incident108','incident109','incident110','incident111','incident112')"
        cursor.execute(f"UPDATE incidents SET is_demo = 0 WHERE id NOT IN {demo_ids}")

        # Backfill assignments & rescue_teams version
        cursor.execute("UPDATE assignments SET version = 1 WHERE version IS NULL OR version = 0")
        cursor.execute("UPDATE rescue_teams SET version = 1 WHERE version IS NULL OR version = 0")

        conn.commit()
        logger.info("Database migrations and backfills applied successfully.")
    finally:
        conn.close()
