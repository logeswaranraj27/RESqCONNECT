from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for, flash

from app.models.core import Incident
from app.repositories.repositories import IncidentRepository, TeamRepository, AssignmentRepository, UserRepository
from app.routes.auth import login_required
from app.services.services import (
    IncidentService, VerificationService, PrioritizationAllocationService,
    AlertCoordinationService, ReportingAuditService, session_app_database,
)

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template("login.html")


@bp.get("/dashboard")
@login_required()
def dashboard():
    role = session["user"]["role"]
    db = session_app_database()
    report = ReportingAuditService(db).overview()
    if role == "Citizen":
        incidents = IncidentRepository(db).list(reporter_id=session["user"]["id"])
        return render_template("citizen_dashboard.html", incidents=incidents, overview=report)
    if role == "Emergency Officer":
        incidents = IncidentRepository(db).list()
        teams = TeamRepository(db).list()
        return render_template("officer_dashboard.html", incidents=incidents, teams=teams, overview=report)
    if role == "Rescue Team":
        assignments = AssignmentRepository(db).list_for_team("team01") + AssignmentRepository(db).list_for_team("team04")
        return render_template("team_dashboard.html", assignments=assignments)
    if role == "Administrator":
        users = UserRepository(db).list_users()
        audit = ReportingAuditService(db).audit_log()
        return render_template("admin_dashboard.html", users=users, audit=audit)
    return render_template("error.html", code=403, message="Unknown role"), 403


@bp.get("/report")
@login_required(["Citizen"])
def report_form():
    return render_template("report.html")


@bp.post("/report")
@login_required(["Citizen"])
def report_post():
    data = request.form
    try:
        lat = float(data["latitude"])
        lng = float(data["longitude"])
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError
        incident = IncidentService(session_app_database()).create(
            data["incident_type"].strip(),
            data["description"].strip(),
            data["address"].strip(),
            lat,
            lng,
            data["severity"].strip(),
            session["user"]["id"],
        )
        flash(f"Incident submitted successfully. ID: {incident.id}", "success")
        return redirect(url_for("main.dashboard"))
    except (KeyError, ValueError):
        flash("Please provide valid incident details and a map location.", "error")
        return render_template("report.html"), 400


@bp.post("/officer/incidents/<incident_id>/verify")
@login_required(["Emergency Officer"])
def verify_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        VerificationService(db).verify(
            incident_id, actor_id, request.form.get("method", "Officer review"),
            request.form.get("result", "Verified"), request.form.get("notes", ""),
        )
        flash("Incident verification recorded.", "success")
    except (ValueError, KeyError) as exc:
        ReportingAuditService(db).audit.log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Verification rejected: {str(exc)}"
        )
        flash(f"Cannot verify incident: {str(exc)}", "error")
    return redirect(url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/prioritize")
@login_required(["Emergency Officer"])
def prioritize_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        PrioritizationAllocationService(db).prioritize(incident_id, actor_id)
        flash("Priority recommendation recorded.", "success")
    except (ValueError, KeyError) as exc:
        ReportingAuditService(db).audit.log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Prioritization rejected: {str(exc)}"
        )
        flash(f"Cannot prioritize incident: {str(exc)}", "error")
    return redirect(url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/assign")
@login_required(["Emergency Officer"])
def assign_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        team_id = request.form.get("team_id")
        if not team_id:
            raise ValueError("Please select a rescue team")
        assignment_id = PrioritizationAllocationService(db).approve_assignment(
            incident_id, team_id, actor_id, request.form.get("reason", ""),
        )
        flash(f"Assignment approved: {assignment_id}", "success")
    except (KeyError, ValueError) as exc:
        ReportingAuditService(db).audit.log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Assignment rejected: {str(exc)}"
        )
        flash(f"Cannot assign incident: {str(exc)}", "error")
    return redirect(url_for("main.dashboard"))


@bp.post("/team/assignments/<assignment_id>/acknowledge")
@login_required(["Rescue Team"])
def acknowledge_assignment(assignment_id):
    db = session_app_database()
    assignment = AssignmentRepository(db).fetchone("SELECT * FROM assignments WHERE id = ?", (assignment_id,))
    if not assignment:
        flash("Assignment not found.", "error")
        return redirect(url_for("main.dashboard"))
    try:
        AlertCoordinationService(db).acknowledge(assignment_id, assignment["team_id"], session["user"]["id"])
        flash("Assignment acknowledged.", "success")
    except PermissionError as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.dashboard"))


@bp.post("/team/assignments/<assignment_id>/status")
@login_required(["Rescue Team"])
def update_team_status(assignment_id):
    db = session_app_database()
    assignment = AssignmentRepository(db).fetchone("SELECT * FROM assignments WHERE id = ?", (assignment_id,))
    if not assignment:
        flash("Assignment not found.", "error")
        return redirect(url_for("main.dashboard"))
    try:
        AlertCoordinationService(db).team_status(
            assignment_id, assignment["team_id"], session["user"]["id"], request.form["status"]
        )
        flash("Team status updated.", "success")
    except (KeyError, ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("main.dashboard"))


@bp.post("/admin/users/<user_id>/role")
@login_required(["Administrator"])
def update_user_role(user_id):
    UserRepository(session_app_database()).set_role(user_id, request.form["role_id"])
    ReportingAuditService(session_app_database()).audit.log(
        session["user"]["id"], "USER_ROLE_UPDATED", "User", user_id, f"Role set to {request.form['role_id']}"
    )
    flash("User role updated.", "success")
    return redirect(url_for("main.dashboard"))


@bp.get("/reports")
@login_required(["Emergency Officer", "Administrator"])
def reports():
    db = session_app_database()
    overview = ReportingAuditService(db).overview()
    audit = ReportingAuditService(db).audit_log(
        request.args.get("action") or None,
        request.args.get("entity_type") or None,
    )
    incidents = IncidentRepository(db).list()
    return render_template("reports.html", overview=overview, audit=audit, incidents=incidents)


@bp.get("/api/dashboard")
@login_required()
def dashboard_api():
    db = session_app_database()
    incidents = IncidentRepository(db).list()
    teams = TeamRepository(db).list()
    overview = ReportingAuditService(db).overview()
    return jsonify({"incidents": incidents, "teams": teams, "overview": overview})
