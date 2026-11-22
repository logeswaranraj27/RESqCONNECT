import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.models.core import Incident
from app.repositories.repositories import IncidentRepository, AuditRepository


@pytest.fixture
def app_instance(tmp_path):
    database = tmp_path / "test_stage1.db"
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{database}",
        "ACKNOWLEDGEMENT_TIMEOUT_SECONDS": 1,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "stage1-secret",
    })
    db = app.extensions["database"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-officer", "Emergency Officer", "Demo"))
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-citizen", "Citizen", "Demo"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("officer1", "officer1", generate_password_hash("demo1234"), "Officer One", "role-officer"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("citizen1", "citizen1", generate_password_hash("demo1234"), "Citizen One", "role-citizen"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO rescue_teams (id, name, capabilities, availability, latitude, longitude, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)"
        ), ("team01", "Team 01", "General", "Available", 13.0, 80.2))
        conn.commit()
    finally:
        conn.close()
    return app


def test_incident106_under_review_verify(app_instance):
    """Reproduce the incident106 bug: incident in 'Under Review' must verify without 500."""
    db = app_instance.extensions["database"]
    IncidentRepository(db).create(Incident(
        id="incident106",
        incident_type="Other",
        description="Large fallen tree and damaged power lines on a residential street.",
        address="Velachery, Chennai",
        latitude=12.9815,
        longitude=80.2180,
        severity="Medium",
        status="Under Review",
        reporter_id="citizen1",
    ))

    client = app_instance.test_client()
    login_res = client.post("/login", data={"username": "officer1", "password": "demo1234"})
    assert login_res.status_code == 302

    res = client.post("/officer/incidents/incident106/verify", data={
        "method": "Officer phone confirmation",
        "result": "Verified",
        "notes": "Confirmed on site",
    })
    assert res.status_code == 302, f"Expected 302 redirect, got {res.status_code}"
    
    updated = IncidentRepository(db).get("incident106")
    assert updated["status"] == "Verified"


def test_all_states_post_verify_no_500(app_instance):
    """Ensure POST /verify across all possible incident states never returns a 500 error."""
    all_states = [
        "Received", "Under Review", "Verified", "Prioritized", "Assigned",
        "In Progress", "Resolved", "Closed", "Cancelled",
        "Verification Required", "Duplicate Review", "Escalated", "Unassigned",
    ]
    db = app_instance.extensions["database"]
    client = app_instance.test_client()
    client.post("/login", data={"username": "officer1", "password": "demo1234"})

    for i, state in enumerate(all_states):
        inc_id = f"inc-state-{i}"
        IncidentRepository(db).create(Incident(
            id=inc_id,
            incident_type="Flood",
            description=f"Testing state {state}",
            address="Chennai",
            latitude=13.0,
            longitude=80.2,
            severity="Medium",
            status=state,
            reporter_id="citizen1",
        ))

        res = client.post(f"/officer/incidents/{inc_id}/verify", data={
            "method": "Officer review",
            "result": "Verified",
            "notes": "Testing",
        })
        assert res.status_code != 500, f"State {state} produced 500 error!"
        assert res.status_code == 302


def test_branded_error_pages(app_instance):
    """Check custom branded error pages (403, 404, 405)."""
    client = app_instance.test_client()
    
    # 404
    r404 = client.get("/nonexistent-page-url")
    assert r404.status_code == 404
    assert b"404" in r404.data
    assert b"ResQConnect" in r404.data
    assert b"112" in r404.data  # disclaimer in footer
    
    # 403 (Citizen attempting officer/admin page)
    client.post("/login", data={"username": "citizen1", "password": "demo1234"})
    r403 = client.get("/reports")
    assert r403.status_code == 403
    assert b"403" in r403.data
    assert b"You do not have permission" in r403.data
    assert b"112" in r403.data
    client.post("/logout")

    # 405
    r405 = client.get("/officer/incidents/incident106/verify")
    assert r405.status_code == 405
    assert b"405" in r405.data
