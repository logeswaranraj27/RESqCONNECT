import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.repositories.repositories import UserRepository, PasswordResetTokenRepository


@pytest.fixture
def auth_app(tmp_path):
    database = tmp_path / "test_auth.db"
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{database}",
        "ACKNOWLEDGEMENT_TIMEOUT_SECONDS": 1,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "stage2-secret",
    })
    db = app.extensions["database"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-citizen", "Citizen", "Reports incidents"))
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-officer", "Emergency Officer", "Verifies incidents"))
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-team", "Rescue Team", "Field responders"))
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-admin", "Administrator", "System settings"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, email, is_active, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1, 1)"
        ), ("admin1", "admin1", generate_password_hash("demo1234"), "Admin One", "role-admin", "admin1@resqconnect.org"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO rescue_teams (id, name, capabilities, availability, latitude, longitude, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)"
        ), ("team01", "Team 01", "General", "Available", 13.0, 80.2))
        conn.commit()
    finally:
        conn.close()
    return app


def test_registration_always_creates_citizen(auth_app):
    client = auth_app.test_client()
    # Attempting to submit role=role-admin should be completely ignored
    res = client.post("/register", data={
        "full_name": "Test Reporter",
        "username": "reporter1",
        "email": "reporter1@example.com",
        "contact_number": "9876543210",
        "password": "Password123!",
        "confirm_password": "Password123!",
        "role": "role-admin",
        "role_id": "role-admin",
    })
    assert res.status_code == 302
    db = auth_app.extensions["database"]
    user = UserRepository(db).get_by_username("reporter1")
    assert user is not None
    assert user["role_name"] == "Citizen"
    assert user["role_id"] == "role-citizen"


def test_registration_validations(auth_app):
    client = auth_app.test_client()

    # 1. Weak password (< 8 chars)
    r1 = client.post("/register", data={
        "full_name": "Short Pass", "username": "shortpass1", "email": "short@example.com",
        "password": "pass", "confirm_password": "pass",
    })
    assert r1.status_code == 400

    # 2. Password equals username
    r2 = client.post("/register", data={
        "full_name": "Equal User", "username": "testuser99", "email": "equal@example.com",
        "password": "testuser99", "confirm_password": "testuser99",
    })
    assert r2.status_code == 400

    # 3. Duplicate username (case-insensitive)
    client.post("/register", data={
        "full_name": "User Alpha", "username": "alpha_user", "email": "alpha@example.com",
        "password": "SecurePassword1", "confirm_password": "SecurePassword1",
    })
    r3 = client.post("/register", data={
        "full_name": "User Alpha Duplicate", "username": "ALPHA_USER", "email": "other@example.com",
        "password": "SecurePassword1", "confirm_password": "SecurePassword1",
    })
    assert r3.status_code == 400

    # 4. Duplicate email (case-insensitive)
    r4 = client.post("/register", data={
        "full_name": "User Beta", "username": "beta_user", "email": "ALPHA@example.com",
        "password": "SecurePassword1", "confirm_password": "SecurePassword1",
    })
    assert r4.status_code == 400

    # 5. Honeypot check
    r5 = client.post("/register", data={
        "full_name": "Bot User", "username": "bot_user", "email": "bot@example.com",
        "password": "SecurePassword1", "confirm_password": "SecurePassword1",
        "website": "http://spamsite.com",
    })
    assert r5.status_code == 400


def test_login_by_username_and_email(auth_app):
    client = auth_app.test_client()
    # Create user
    client.post("/register", data={
        "full_name": "Multi Login", "username": "multilogin", "email": "multi@example.com",
        "password": "Password123!", "confirm_password": "Password123!",
    })
    client.post("/logout")

    # Login by username
    r_user = client.post("/login", data={"username": "multilogin", "password": "Password123!"})
    assert r_user.status_code == 302
    client.post("/logout")

    # Login by email
    r_email = client.post("/login", data={"username": "multi@example.com", "password": "Password123!"})
    assert r_email.status_code == 302
    client.post("/logout")

    # Wrong password
    r_bad = client.post("/login", data={"username": "multilogin", "password": "WrongPassword"})
    assert r_bad.status_code == 401


def test_lockout_after_repeated_failures(auth_app):
    client = auth_app.test_client()
    db = auth_app.extensions["database"]
    
    # 5 consecutive failed logins
    for _ in range(5):
        res = client.post("/login", data={"username": "admin1", "password": "badpassword"})
        assert res.status_code == 401

    # 6th attempt should return lockout message
    res6 = client.post("/login", data={"username": "admin1", "password": "badpassword"})
    assert res6.status_code == 401
    assert b"locked" in res6.data.lower()

    # Even correct password should now be blocked while locked
    res_correct_while_locked = client.post("/login", data={"username": "admin1", "password": "demo1234"})
    assert res_correct_while_locked.status_code == 401
    assert b"locked" in res_correct_while_locked.data.lower()


def test_password_reset_flow(auth_app):
    client = auth_app.test_client()
    db = auth_app.extensions["database"]

    # Request reset
    res = client.post("/forgot-password", data={"identifier": "admin1"})
    assert res.status_code == 200
    assert b"[SIMULATED EMAIL DELIVERY]" in res.data

    # Extract token link from response
    token_repo = PasswordResetTokenRepository(db)
    user = UserRepository(db).get_by_username("admin1")
    tokens = db.connect().cursor().execute(
        "SELECT * FROM password_reset_tokens WHERE user_id = ?", (user["id"],)
    ).fetchall()
    assert len(tokens) == 1

    # Use service to reset
    from app.services.services import AuthRoleService
    service = AuthRoleService(db)
    raw_token, _ = service.request_password_reset("admin1")
    service.reset_password_with_token(raw_token, "NewAdminPass123!", "NewAdminPass123!")

    # Login with new password
    r_login = client.post("/login", data={"username": "admin1", "password": "NewAdminPass123!"})
    assert r_login.status_code == 302

    # Old password fails
    client.post("/logout")
    r_old = client.post("/login", data={"username": "admin1", "password": "demo1234"})
    assert r_old.status_code == 401


def test_admin_role_guards(auth_app):
    client = auth_app.test_client()
    client.post("/login", data={"username": "admin1", "password": "demo1234"})
    
    # Admin cannot demote self
    r_self_demote = client.post("/admin/users/admin1/role", data={"role_id": "role-citizen"})
    assert r_self_demote.status_code == 302
    db = auth_app.extensions["database"]
    admin = UserRepository(db).get("admin1")
    assert admin["role_id"] == "role-admin"

    # Admin cannot deactivate self
    r_self_deactivate = client.post("/admin/users/admin1/status", data={"is_active": "0"})
    assert r_self_deactivate.status_code == 302
    admin = UserRepository(db).get("admin1")
    assert admin["is_active"] == 1
