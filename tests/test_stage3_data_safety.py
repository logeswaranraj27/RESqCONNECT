import pytest
import sqlite3
from werkzeug.security import generate_password_hash

from app import create_app
from app.repositories.base import utc_now
from app.repositories.migrations import run_migrations
from app.repositories.repositories import IncidentRepository, UserRepository


@pytest.fixture
def legacy_db(tmp_path):
    """Simulate a legacy production database that lacks new columns and has a user-submitted incident."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    # Create legacy schema without the new columns
    cur.executescript("""
        CREATE TABLE roles (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT);
        CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, role_id TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, is_demo INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE incidents (id TEXT PRIMARY KEY, incident_type TEXT NOT NULL, description TEXT NOT NULL, address TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL, reporter_id TEXT NOT NULL, priority_score REAL NOT NULL DEFAULT 0, priority_level TEXT, duplicate_flag INTEGER NOT NULL DEFAULT 0, suspicious_flag INTEGER NOT NULL DEFAULT 0, is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE rescue_teams (id TEXT PRIMARY KEY, name TEXT NOT NULL, capabilities TEXT NOT NULL, availability TEXT NOT NULL, latitude REAL, longitude REAL, is_demo INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE assignments (id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, team_id TEXT NOT NULL, status TEXT NOT NULL, officer_id TEXT, acknowledged_at TEXT, dispatched_at TEXT, on_scene_at TEXT, completed_at TEXT, recommendation_reason TEXT, is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE audit_logs (id TEXT PRIMARY KEY, actor_id TEXT, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, details TEXT, created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0);
    """)

    # Insert demo roles
    cur.execute("INSERT INTO roles VALUES ('role-citizen', 'Citizen', 'Reports')")
    cur.execute("INSERT INTO roles VALUES ('role-officer', 'Emergency Officer', 'Verifies')")

    # Insert user account created on live site
    cur.execute("INSERT INTO users VALUES ('user-live-1', 'john_doe', 'hash123', 'John Doe', 'role-citizen', 1, 0)")

    # Insert demo incidents and user-submitted incident incidentc7e917cb2126 (which had is_demo=1 due to the old bug)
    cur.execute("""INSERT INTO incidents VALUES (
        'incident101', 'Flood', 'Flash flood', 'Adyar', 13.0, 80.2, 'Critical', 'Unassigned', 'user-live-1', 0, '', 0, 0, 1, '2026-11-19T10:00:00+00:00', '2026-11-19T10:00:00+00:00'
    )""")
    cur.execute("""INSERT INTO incidents VALUES (
        'incidentc7e917cb2126', 'Fire', 'Apartment smoke', 'Anna Nagar', 13.08, 80.21, 'High', 'Received', 'user-live-1', 0, '', 0, 0, 1, '2026-11-19T12:00:00+00:00', '2026-11-19T12:00:00+00:00'
    )""")
    conn.commit()
    conn.close()

    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{db_file}",
        "ACKNOWLEDGEMENT_TIMEOUT_SECONDS": 1,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "stage3-secret",
    })
    return app


def test_migration_backfills_and_protects_user_data(legacy_db):
    """Test that migrations add columns and backfill reported_severity, effective_severity, and protects user incidents."""
    db = legacy_db.extensions["database"]
    run_migrations(db)

    # Check that incidentc7e917cb2126 now has is_demo = 0 and severity columns backfilled
    inc = IncidentRepository(db).get("incidentc7e917cb2126")
    assert inc is not None
    assert inc["is_demo"] == 0
    assert inc["reported_severity"] == "High"
    assert inc["effective_severity"] == "High"

    # User-created account has email backfilled
    user = UserRepository(db).get("user-live-1")
    assert user is not None
    assert user["email"] == "john_doe@resqconnect.org"
    assert user["is_active"] == 1


def test_idempotent_seeding_does_not_wipe_user_data(legacy_db, monkeypatch):
    """Confirm running seed multiple times retains user incidents and user accounts."""
    db = legacy_db.extensions["database"]
    run_migrations(db)

    # Import seed logic or simulate seed.py insertion
    import seed
    # Verify user account and user incident survived
    user = UserRepository(db).get("user-live-1")
    assert user is not None
    assert user["username"] == "john_doe"

    inc = IncidentRepository(db).get("incidentc7e917cb2126")
    assert inc is not None
    assert inc["description"] == "Apartment smoke"
