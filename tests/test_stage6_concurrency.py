import concurrent.futures
import time
import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.repositories.repositories import IncidentRepository, TeamRepository, AssignmentRepository
from app.services.services import AlertCoordinationService, IncidentService, PrioritizationAllocationService, VerificationService


@pytest.fixture
def conc_app(tmp_path):
    database = tmp_path / "test_conc.db"
    app = create_app({
        "TESTING": True,
        "DATABASE_URL": f"sqlite:///{database}",
        "ACKNOWLEDGEMENT_TIMEOUT_SECONDS": 1,
        "WTF_CSRF_ENABLED": False,
        "SECRET_KEY": "conc-secret",
    })
    db = app.extensions["database"]
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-officer", "Emergency Officer", "Officer"))
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-citizen", "Citizen", "Citizen"))
        cur.execute(db.placeholder_sql("INSERT INTO roles (id, name, description) VALUES (?, ?, ?)"),
                    ("role-team", "Rescue Team", "Team"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("officer1", "officer1", generate_password_hash("demo1234"), "Officer 1", "role-officer"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("officer2", "officer2", generate_password_hash("demo1234"), "Officer 2", "role-officer"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, 1)"
        ), ("citizen1", "citizen1", generate_password_hash("demo1234"), "Citizen 1", "role-citizen"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, team_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1, 1)"
        ), ("rescue1", "rescue1", generate_password_hash("demo1234"), "Rescue 1", "role-team", "teamConc1"))
        cur.execute(db.placeholder_sql(
            "INSERT INTO rescue_teams (id, name, capabilities, availability, latitude, longitude, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)"
        ), ("teamConc1", "Team Conc 1", "General,Medical", "Available", 13.0, 80.2))
        cur.execute(db.placeholder_sql(
            "INSERT INTO rescue_teams (id, name, capabilities, availability, latitude, longitude, is_demo) VALUES (?, ?, ?, ?, ?, ?, 1)"
        ), ("teamConc2", "Team Conc 2", "FireRescue", "Available", 13.0, 80.2))
        conn.commit()
    finally:
        conn.close()
    return app


def test_concurrent_incident_approval_collision(conc_app):
    """Two officers trying to approve the same incident simultaneously: exactly one succeeds."""
    db = conc_app.extensions["database"]
    inc = IncidentService(db).create("Fire", "Warehouse blaze", "Ambattur", 13.11, 80.15, "High", "citizen1")
    VerificationService(db).verify(inc.id, "officer1", "Officer review", "Verified")
    PrioritizationAllocationService(db).prioritize(inc.id, "officer1")

    successes = []
    failures = []

    def try_assign(officer_id):
        alloc = PrioritizationAllocationService(db)
        try:
            asg_id = alloc.approve_assignment(inc.id, "teamConc1", officer_id)
            successes.append(asg_id)
        except ValueError as exc:
            failures.append(str(exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_assign, "officer1")
        f2 = executor.submit(try_assign, "officer2")
        f1.result()
        f2.result()

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected exactly 1 handled failure, got {len(failures)}"
    assert "already" in failures[0].lower() or "committed" in failures[0].lower()


def test_team_cannot_be_assigned_to_two_incidents_simultaneously(conc_app):
    """A team already committed to an active incident cannot be assigned to another."""
    db = conc_app.extensions["database"]
    inc1 = IncidentService(db).create("Fire", "Fire 1", "Site 1", 13.0, 80.2, "High", "citizen1")
    inc2 = IncidentService(db).create("Fire", "Fire 2", "Site 2", 13.0, 80.2, "High", "citizen1")

    VerificationService(db).verify(inc1.id, "officer1", "Review", "Verified")
    VerificationService(db).verify(inc2.id, "officer1", "Review", "Verified")

    alloc = PrioritizationAllocationService(db)
    alloc.prioritize(inc1.id, "officer1")
    alloc.prioritize(inc2.id, "officer1")

    # Assign teamConc2 to inc1
    alloc.approve_assignment(inc1.id, "teamConc2", "officer1")

    # Attempting to assign same teamConc2 to inc2 must fail
    with pytest.raises(ValueError) as excinfo:
        alloc.approve_assignment(inc2.id, "teamConc2", "officer1")
    assert "already committed" in str(excinfo.value).lower()


def test_scheduler_lock_prevents_duplicate_escalation(conc_app):
    """Multiple threads running timeout escalation simultaneously must only trigger escalation once."""
    db = conc_app.extensions["database"]
    inc = IncidentService(db).create("Flood", "Flood timeout", "Chennai", 13.0, 80.2, "Critical", "citizen1")
    VerificationService(db).verify(inc.id, "officer1", "Review", "Verified")
    alloc = PrioritizationAllocationService(db)
    alloc.prioritize(inc.id, "officer1")
    asg_id = alloc.approve_assignment(inc.id, "teamConc1", "officer1")

    # Sleep so timeout expires (timeout is 1 second in test)
    time.sleep(1.2)

    results = []
    def run_escalation():
        coord = AlertCoordinationService(db)
        return coord.escalate_expired(1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run_escalation) for _ in range(4)]
        for f in futures:
            results.append(f.result())

    total_escalated = sum(results)
    assert total_escalated == 1, f"Expected exactly 1 total escalation across all threads, got {total_escalated}"


def test_ten_concurrent_dashboard_polling(conc_app):
    """Simulate 10 concurrent open dashboards polling /api/updates without blocking."""
    client_pool = [conc_app.test_client() for _ in range(10)]
    
    # Log in each client
    for i, c in enumerate(client_pool):
        c.post("/login", data={"username": "officer1", "password": "demo1234"})

    def poll_dashboard(c):
        resp = c.get("/api/updates")
        return resp.status_code, resp.json

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(poll_dashboard, c) for c in client_pool]
        results = [f.result() for f in futures]

    for status_code, data in results:
        assert status_code == 200
        assert "timestamp" in data
        assert "critical_unassigned_count" in data


def test_multi_user_live_simulation(conc_app):
    """Multi-user simulation: Citizen reports -> Officer assigns -> Team acknowledges -> Officer & Citizen observe live update."""
    citizen_client = conc_app.test_client()
    officer_client = conc_app.test_client()
    team_client = conc_app.test_client()

    # Log in 3 separate users with isolated sessions
    citizen_client.post("/login", data={"username": "citizen1", "password": "demo1234"})
    officer_client.post("/login", data={"username": "officer1", "password": "demo1234"})
    team_client.post("/login", data={"username": "rescue1", "password": "demo1234"})

    # 1. Citizen reports critical incident
    r_rep = citizen_client.post("/report", data={
        "incident_type": "Flood", "description": "Families trapped by flood",
        "address": "Saidapet lowlands", "latitude": "13.02", "longitude": "80.22",
        "severity": "Critical",
    })
    assert r_rep.status_code == 302

    # 2. Officer polls updates and sees 1 critical unassigned incident
    r_poll1 = officer_client.get("/api/updates")
    assert r_poll1.status_code == 200
    assert r_poll1.json["critical_unassigned_count"] >= 1
    new_inc_id = r_poll1.json["critical_unassigned"][0]["id"]

    # 3. Officer verifies and assigns teamConc1
    officer_client.post(f"/officer/incidents/{new_inc_id}/verify", data={"result": "Verified", "method": "Phone"})
    officer_client.post(f"/officer/incidents/{new_inc_id}/prioritize")
    officer_client.post(f"/officer/incidents/{new_inc_id}/assign", data={"team_id": "teamConc1"})

    # 4. Team polls and sees notification + assignment
    r_team_poll = team_client.get("/api/notifications")
    assert r_team_poll.status_code == 200
    assert len(r_team_poll.json) >= 1

    # Team acknowledges
    db = conc_app.extensions["database"]
    asg = AssignmentRepository(db).get_active_for_incident(new_inc_id)
    assert asg is not None
    r_ack = team_client.post(f"/team/assignments/{asg['id']}/acknowledge")
    assert r_ack.status_code == 302

    # 5. Officer checks status: assignment is now Acknowledged, incident is In Progress
    updated_inc = IncidentRepository(db).get(new_inc_id)
    assert updated_inc["status"] == "In Progress"

    # Team marks On Scene
    team_client.post(f"/team/assignments/{asg['id']}/status", data={"status": "On Scene"})
    updated_asg = AssignmentRepository(db).get(asg["id"])
    assert updated_asg["status"] == "On Scene"

    # Team marks Completed
    team_client.post(f"/team/assignments/{asg['id']}/status", data={"status": "Completed"})
    assert IncidentRepository(db).get(new_inc_id)["status"] == "Resolved"
    assert TeamRepository(db).get("teamConc1")["availability"] == "Available"
