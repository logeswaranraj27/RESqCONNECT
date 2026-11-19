import os
import tempfile
import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.models.core import Incident
from app.services.services import (
    IncidentService, VerificationService, PrioritizationAllocationService,
    AlertCoordinationService,
)
from app.repositories.repositories import IncidentRepository, AuditRepository


@pytest.fixture
def app(tmp_path):
    database = tmp_path / "test.db"
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{database}",
        "ACKNOWLEDGEMENT_TIMEOUT_SECONDS": 1,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "test-secret",
    })
    db = app.extensions["database"]
    connection = db.connect()
    try:
        cursor = connection.cursor()
        cursor.execute(db.placeholder_sql(
            "INSERT INTO roles (id,name,description) VALUES (?, ?, ?)"
        ), ("role-citizen", "Citizen", "Demo"))
        cursor.execute(db.placeholder_sql(
            "INSERT INTO roles (id,name,description) VALUES (?, ?, ?)"
        ), ("role-officer", "Emergency Officer", "Demo"))
        cursor.execute(db.placeholder_sql(
            "INSERT INTO users (id,username,password_hash,display_name,role_id,is_active,is_demo) "
            "VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("citizen1", "citizen1", generate_password_hash("demo1234"), "Demo Citizen", "role-citizen"))
        cursor.execute(db.placeholder_sql(
            "INSERT INTO users (id,username,password_hash,display_name,role_id,is_active,is_demo) "
            "VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("officer1", "officer1", generate_password_hash("demo1234"), "Demo Officer", "role-officer"))
        cursor.execute(db.placeholder_sql(
            "INSERT INTO rescue_teams (id,name,capabilities,availability,latitude,longitude,is_demo) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)"
        ), ("team04", "FloodRescue Team 04", "FloodRescue,Boat", "Available", 13.0878, 80.2785))
        connection.commit()
    finally:
        connection.close()
    return app


def test_login_and_citizen_report(app):
    client = app.test_client()
    response = client.post("/login", data={"username": "citizen1", "password": "demo1234"})
    assert response.status_code == 302
    response = client.post("/report", data={
        "incident_type": "Flood",
        "description": "Road flooding with families waiting for help.",
        "address": "Adyar, Chennai",
        "latitude": "13.0067",
        "longitude": "80.2206",
        "severity": "Critical",
    })
    assert response.status_code == 302
    incident = IncidentRepository(app.extensions["database"]).list(reporter_id="citizen1")[0]
    assert incident["id"].startswith("incident")


def test_duplicate_is_flagged_not_rejected(app):
    db = app.extensions["database"]
    IncidentRepository(db).create(Incident(
        id="incident-existing", incident_type="Flood",
        description="Severe flooding near low lying homes",
        address="Adyar", latitude=13.0067, longitude=80.2206,
        severity="Critical", status="Received", reporter_id="citizen1",
        created_at="2026-11-19T10:00:00+00:00",
    ))
    connection = db.connect()
    try:
        connection.execute(
            db.placeholder_sql("UPDATE incidents SET created_at = ? WHERE id = ?"),
            ("2026-11-19T10:00:00+00:00", "incident-existing"),
        )
        connection.commit()
    finally:
        connection.close()
    incident = Incident(
        id="incident-new", incident_type="Flood",
        description="Severe flooding near low lying homes",
        address="Adyar", latitude=13.0068, longitude=80.2207,
        severity="Critical", status="Received", reporter_id="citizen1",
        created_at="2026-11-19T10:10:00+00:00",
    )
    assert IncidentService(db).duplicate_exists(incident) is True
    assert IncidentRepository(db).get("incident-existing")["status"] == "Received"


def test_illegal_transition_blocked(app):
    db = app.extensions["database"]
    IncidentRepository(db).create(Incident(
        id="incident-transition", incident_type="Fire", description="Building fire",
        address="Chennai", latitude=13.0, longitude=80.2, severity="High",
        status="Received", reporter_id="citizen1",
    ))
    with pytest.raises(ValueError):
        VerificationService(db).transition("incident-transition", "officer1", "Resolved")


def test_officer_allocation_and_team_acknowledge(app):
    db = app.extensions["database"]
    incident = Incident(
        id="incident101", incident_type="Flood", description="Stranded families",
        address="Adyar", latitude=13.0067, longitude=80.2206, severity="Critical",
        status="Verified", reporter_id="citizen1",
    )
    IncidentRepository(db).create(incident)
    service = PrioritizationAllocationService(db)
    service.prioritize(incident.id, "officer1")
    team, reason = service.recommend_team(IncidentRepository(db).get(incident.id))
    assert team["id"] == "team04"
    assignment_id = service.approve_assignment(incident.id, team["id"], "officer1")
    AlertCoordinationService(db).acknowledge(assignment_id, "team04", "rescue1")
    assignment = service.assignments.fetchone("SELECT * FROM assignments WHERE id = ?", (assignment_id,))
    assert assignment["status"] == "Acknowledged"


def test_acknowledgement_timeout_escalates_once(app):
    db = app.extensions["database"]
    IncidentRepository(db).create(Incident(
        id="incident-timeout", incident_type="Flood", description="Flood emergency",
        address="Chennai", latitude=13.0, longitude=80.2, severity="Critical",
        status="Assigned", reporter_id="citizen1",
    ))
    service = PrioritizationAllocationService(db)
    assignment_id = service.assignments.fetchone(
        "SELECT id FROM assignments WHERE incident_id = 'incident-timeout'"
    )
    if assignment_id is None:
        service.assignments.create_with_id(
            "assignment-timeout", "incident-timeout", "team04", "PendingAcknowledgement",
            "officer1", "Demo timeout test",
        )
    assert AlertCoordinationService(db).escalate_expired(0) >= 1
    assert AlertCoordinationService(db).escalate_expired(0) == 0
