import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.models.core import Incident
from app.repositories.repositories import IncidentRepository, TeamRepository, AssignmentRepository, AuditRepository
from app.services.services import IncidentService, PrioritizationAllocationService, ReportingAuditService


@pytest.fixture
def ops_app(tmp_path):
    database = tmp_path / "test_ops.db"
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{database}",
        "ACKNOWLEDGEMENT_TIMEOUT_SECONDS": 1,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "ops-secret",
    })
    db = app.extensions["database"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-officer", "Emergency Officer", "Officer"))
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-citizen", "Citizen", "Citizen"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("officer1", "officer1", generate_password_hash("demo1234"), "Officer One", "role-officer"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("citizen1", "citizen1", generate_password_hash("demo1234"), "Citizen One", "role-citizen"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO rescue_teams (id, name, capabilities, availability, latitude, longitude, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)"
        ), ("teamA", "Alpha Rescue", "FireRescue,Medical", "Available", 13.0, 80.2))
        cur.execute(db.placeholder_sql(
            "INSERT INTO rescue_teams (id, name, capabilities, availability, latitude, longitude, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)"
        ), ("teamB", "Bravo Rescue", "FloodRescue,Boat", "Available", 13.0, 80.2))
        conn.commit()
    finally:
        conn.close()
    return app


def test_severity_and_critical_unassigned_counts(ops_app):
    db = ops_app.extensions["database"]
    inc_service = IncidentService(db)
    report_service = ReportingAuditService(db)
    alloc_service = PrioritizationAllocationService(db)

    # 1. Initially 0 critical unassigned
    assert report_service.overview()["critical_unassigned_count"] == 0

    # 2. Citizen reports Medium incident
    inc = inc_service.create("Flood", "Rising floodwaters", "Velachery", 12.98, 80.21, "Medium", "citizen1")
    assert report_service.overview()["critical_unassigned_count"] == 0

    # 3. Officer escalates effective severity to Critical (with mandatory reason)
    inc_service.update_severity(inc.id, "officer1", "Critical", reason="Ground team confirms water level rising dangerously fast")
    overview = report_service.overview()
    assert overview["critical_unassigned_count"] == 1
    assert len(overview["critical_unassigned"]) == 1
    assert overview["critical_unassigned"][0]["id"] == inc.id

    # 4. Officer verifies and assigns team -> critical unassigned count drops to 0
    from app.services.services import VerificationService
    VerificationService(db).verify(inc.id, "officer1", "Officer review", "Verified")
    alloc_service.approve_assignment(inc.id, "teamB", "officer1", "Approved")
    overview2 = report_service.overview()
    assert overview2["critical_unassigned_count"] == 0


def test_flag_resolution_workflow(ops_app):
    db = ops_app.extensions["database"]
    inc_service = IncidentService(db)

    # 1. Create flagged duplicate incident
    inc = inc_service.create("Fire", "Fire in building", "Anna Nagar", 13.08, 80.21, "High", "citizen1")
    # Mark genuine
    inc_service.resolve_flags(inc.id, "officer1", "genuine", notes="Verified by direct telephone call")
    updated = IncidentRepository(db).get(inc.id)
    assert updated["duplicate_flag"] == 0
    assert updated["status"] == "Verified"

    # 2. Mark false report requires reason and cancels incident
    inc2 = inc_service.create("Fire", "fake test fire prank", "Saidapet", 13.02, 80.22, "Low", "citizen1")
    inc_service.resolve_flags(inc2.id, "officer1", "false_report", notes="Confirmed prank call by caller ID")
    updated2 = IncidentRepository(db).get(inc2.id)
    assert updated2["status"] == "Cancelled"
    assert "prank" in (updated2["cancellation_reason"] or "").lower()


def test_team_reassignment_releases_previous_team_and_notifies(ops_app):
    db = ops_app.extensions["database"]
    alloc_service = PrioritizationAllocationService(db)
    teams = TeamRepository(db)
    assignments = AssignmentRepository(db)

    inc = IncidentService(db).create("Fire", "Factory fire", "Guindy", 13.01, 80.21, "High", "citizen1")
    IncidentRepository(db).update_status(inc.id, "Prioritized")

    # Initial assignment to teamA
    asg1_id = alloc_service.approve_assignment(inc.id, "teamA", "officer1")
    assert teams.get("teamA")["availability"] == "Committed"

    # Reassign to teamB
    asg2_id = alloc_service.reassign_team(inc.id, "teamB", "officer1", reason="teamA delayed due to traffic block")
    
    # Previous teamA must be released to Available
    assert teams.get("teamA")["availability"] == "Available"
    # New teamB must be Committed
    assert teams.get("teamB")["availability"] == "Committed"
    # Old assignment cancelled, new assignment active
    assert assignments.get(asg1_id)["status"] == "Cancelled"
    assert assignments.get(asg2_id)["status"] == "PendingAcknowledgement"


def test_incident_reopen_and_close(ops_app):
    db = ops_app.extensions["database"]
    inc_service = IncidentService(db)
    inc_repo = IncidentRepository(db)

    inc = inc_service.create("Other", "Tree fallen", "Mylapore", 13.03, 80.26, "Low", "citizen1")
    inc_repo.update_status(inc.id, "Resolved")

    # Close incident
    inc_service.close(inc.id, "officer1", notes="Site cleared")
    assert inc_repo.get(inc.id)["status"] == "Closed"

    # Reopen incident
    inc_service.reopen(inc.id, "officer1", reason="Secondary power pole damaged")
    updated = inc_repo.get(inc.id)
    assert updated["status"] == "Under Review"
    assert "Secondary power pole" in updated["officer_notes"]
