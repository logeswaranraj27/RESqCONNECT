from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for, flash, abort

from app.models.core import Incident
from app.repositories.base import utc_now
from app.repositories.repositories import (
    AssignmentRepository, AuditRepository, IncidentRepository,
    NotificationRepository, TeamRepository, UserRepository,
)
from app.routes.auth import login_required
from app.services.services import (
    AlertCoordinationService, AuthRoleService, IncidentService,
    PrioritizationAllocationService, ReportingAuditService,
    VerificationService, session_app_database,
)

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    if session.get("user"):
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.get("/dashboard")
@login_required()
def dashboard():
    role = session["user"]["role"]
    db = session_app_database()
    overview = ReportingAuditService(db).overview()

    if role == "Citizen":
        incidents = IncidentRepository(db).list(reporter_id=session["user"]["id"])
        return render_template("citizen_dashboard.html", incidents=incidents, overview=overview)

    if role == "Emergency Officer":
        incidents = IncidentRepository(db).list()
        teams = TeamRepository(db).list()
        # Find recommendation for prioritized incidents without assignment
        recs = {}
        alloc_service = PrioritizationAllocationService(db)
        for inc in incidents:
            if inc["status"] in ("Prioritized", "Unassigned", "Verified", "Escalated"):
                team, reason = alloc_service.recommend_team(inc)
                recs[inc["id"]] = {"team": team, "reason": reason}

        # Active assignment map
        assignment_map = {}
        for inc in incidents:
            act = AssignmentRepository(db).get_active_for_incident(inc["id"])
            if act:
                assignment_map[inc["id"]] = act

        return render_template(
            "officer_dashboard.html",
            incidents=incidents,
            teams=teams,
            overview=overview,
            recommendations=recs,
            assignments=assignment_map,
        )

    if role == "Rescue Team":
        user_team_id = session["user"].get("team_id")
        teams = TeamRepository(db).list()
        if user_team_id:
            assignments = AssignmentRepository(db).list_by_team(user_team_id)
            current_team = TeamRepository(db).get(user_team_id)
        else:
            assignments = AssignmentRepository(db).list_by_team("team04")
            current_team = TeamRepository(db).get("team04")
        return render_template("team_dashboard.html", assignments=assignments, current_team=current_team, teams=teams)

    if role == "Administrator":
        users = UserRepository(db).list_users()
        teams = TeamRepository(db).list()
        audit = ReportingAuditService(db).audit_log(limit=50)
        return render_template("admin_dashboard.html", users=users, teams=teams, audit=audit, overview=overview)

    abort(403)


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
            raise ValueError("Coordinates out of range")
        incident = IncidentService(session_app_database()).create(
            incident_type=data["incident_type"].strip(),
            description=data["description"].strip(),
            address=data["address"].strip(),
            latitude=lat,
            longitude=lng,
            severity=data["severity"].strip(),
            reporter_id=session["user"]["id"],
        )
        flash(f"Incident submitted successfully. Incident ID: {incident.id}", "success")
        return redirect(url_for("main.dashboard"))
    except (KeyError, ValueError) as exc:
        flash(f"Please provide valid incident details and a map location. ({str(exc)})", "error")
        return render_template("report.html"), 400


@bp.get("/incidents/<incident_id>")
@login_required()
def incident_details(incident_id):
    db = session_app_database()
    incident = IncidentRepository(db).get(incident_id)
    if not incident:
        abort(404)

    # Citizen privacy check: citizens can only view their own reports
    if session["user"]["role"] == "Citizen" and incident["reporter_id"] != session["user"]["id"]:
        abort(403)

    verifications = db.connect().cursor().execute(
        db.placeholder_sql("SELECT * FROM verification_records WHERE incident_id = ? ORDER BY reviewed_at DESC"),
        (incident_id,),
    ).fetchall()

    assignments = db.connect().cursor().execute(
        db.placeholder_sql(
            """SELECT assignments.*, rescue_teams.name AS team_name
            FROM assignments
            JOIN rescue_teams ON rescue_teams.id = assignments.team_id
            WHERE assignments.incident_id = ? ORDER BY assignments.created_at DESC"""
        ),
        (incident_id,),
    ).fetchall()

    audit_history = AuditRepository(db).list(entity_type="Incident", limit=50)
    audit_history = [log for log in audit_history if log["entity_id"] == incident_id]

    active_assignment = AssignmentRepository(db).get_active_for_incident(incident_id)
    teams = TeamRepository(db).list()
    alloc_service = PrioritizationAllocationService(db)
    rec_team, rec_reason = alloc_service.recommend_team(incident)

    return render_template(
        "incident_details.html",
        incident=incident,
        verifications=verifications,
        assignments=assignments,
        active_assignment=active_assignment,
        audit_history=audit_history,
        teams=teams,
        rec_team=rec_team,
        rec_reason=rec_reason,
    )


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
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Verification rejected: {str(exc)}"
        )
        flash(f"Cannot verify incident: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/prioritize")
@login_required(["Emergency Officer"])
def prioritize_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        confirmed_severity = request.form.get("severity")
        reason = request.form.get("reason", "")
        PrioritizationAllocationService(db).prioritize(
            incident_id, actor_id, confirmed_severity=confirmed_severity, reason=reason
        )
        flash("Priority recommendation and severity recorded.", "success")
    except (ValueError, KeyError) as exc:
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Prioritization rejected: {str(exc)}"
        )
        flash(f"Cannot prioritize incident: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


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
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Assignment rejected: {str(exc)}"
        )
        flash(f"Cannot assign incident: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/reassign")
@login_required(["Emergency Officer"])
def reassign_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        new_team_id = request.form.get("new_team_id")
        reason = request.form.get("reason", "")
        if not new_team_id:
            raise ValueError("Please select a new rescue team")
        assignment_id = PrioritizationAllocationService(db).reassign_team(
            incident_id, new_team_id, actor_id, reason=reason
        )
        flash(f"Team reassigned successfully (Assignment: {assignment_id})", "success")
    except (KeyError, ValueError) as exc:
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Reassignment rejected: {str(exc)}"
        )
        flash(f"Cannot reassign team: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/cancel-assignment")
@login_required(["Emergency Officer"])
def cancel_assignment(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        assignment_id = request.form.get("assignment_id")
        reason = request.form.get("reason", "")
        if not assignment_id:
            raise ValueError("Assignment ID required")
        PrioritizationAllocationService(db).cancel_assignment(assignment_id, actor_id, reason=reason)
        flash("Assignment cancelled and team released.", "success")
    except (KeyError, ValueError) as exc:
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Assignment cancellation rejected: {str(exc)}"
        )
        flash(f"Cannot cancel assignment: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/resolve-flag")
@login_required(["Emergency Officer"])
def resolve_flag(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        action = request.form.get("action")
        notes = request.form.get("notes", "")
        duplicate_of = request.form.get("duplicate_of")
        IncidentService(db).resolve_flags(incident_id, actor_id, action, notes=notes, duplicate_of=duplicate_of)
        flash("Incident flag resolved.", "success")
    except (KeyError, ValueError) as exc:
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Flag resolution rejected: {str(exc)}"
        )
        flash(f"Cannot resolve flag: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/cancel")
@login_required(["Emergency Officer"])
def cancel_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        reason = request.form.get("reason", "")
        IncidentService(db).cancel(incident_id, actor_id, reason=reason)
        flash("Incident cancelled.", "success")
    except (KeyError, ValueError) as exc:
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Cancel rejected: {str(exc)}"
        )
        flash(f"Cannot cancel incident: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/close")
@login_required(["Emergency Officer"])
def close_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        notes = request.form.get("notes", "")
        IncidentService(db).close(incident_id, actor_id, notes=notes)
        flash("Incident closed.", "success")
    except (KeyError, ValueError) as exc:
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Close rejected: {str(exc)}"
        )
        flash(f"Cannot close incident: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/reopen")
@login_required(["Emergency Officer"])
def reopen_incident(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    try:
        reason = request.form.get("reason", "")
        IncidentService(db).reopen(incident_id, actor_id, reason=reason)
        flash("Incident reopened to Under Review.", "success")
    except (KeyError, ValueError) as exc:
        AuditRepository(db).log(
            actor_id, "ACTION_REJECTED", "Incident", incident_id, f"Reopen rejected: {str(exc)}"
        )
        flash(f"Cannot reopen incident: {str(exc)}", "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.post("/officer/incidents/<incident_id>/note")
@login_required(["Emergency Officer"])
def add_note(incident_id):
    db = session_app_database()
    actor_id = session["user"]["id"]
    note = request.form.get("note", "").strip()
    if note:
        IncidentRepository(db).update_status(incident_id, IncidentRepository(db).get(incident_id)["status"], notes=note)
        AuditRepository(db).log(actor_id, "NOTE_ADDED", "Incident", incident_id, note)
        flash("Note recorded.", "success")
    return redirect(request.referrer or url_for("main.dashboard"))


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
        flash("Assignment acknowledged. Status set to In Progress.", "success")
    except (PermissionError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("main.dashboard"))


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
        flash(f"Team status updated to {request.form['status']}.", "success")
    except (KeyError, ValueError, PermissionError) as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.get("/admin/users")
@login_required(["Administrator"])
def admin_users():
    db = session_app_database()
    role_filter = request.args.get("role_id")
    search = request.args.get("search")
    users = UserRepository(db).list_users(role_id=role_filter, search=search)
    teams = TeamRepository(db).list()
    return render_template("admin_users.html", users=users, teams=teams, role_filter=role_filter, search=search)


@bp.post("/admin/users/<user_id>/role")
@login_required(["Administrator"])
def update_user_role(user_id):
    db = session_app_database()
    service = AuthRoleService(db)
    admin_id = session["user"]["id"]
    try:
        service.update_user_role(
            admin_user_id=admin_id,
            target_user_id=user_id,
            new_role_id=request.form.get("role_id"),
            team_id=request.form.get("team_id") or None,
        )
        flash("User role and team assignment updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("main.admin_users"))


@bp.post("/admin/users/<user_id>/status")
@login_required(["Administrator"])
def update_user_status(user_id):
    db = session_app_database()
    service = AuthRoleService(db)
    admin_id = session["user"]["id"]
    is_active = request.form.get("is_active") == "1"
    try:
        service.set_user_active(admin_user_id=admin_id, target_user_id=user_id, is_active=is_active)
        flash("User active status updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("main.admin_users"))


@bp.post("/admin/users/<user_id>/reset-password")
@login_required(["Administrator"])
def admin_reset_user_password(user_id):
    db = session_app_database()
    service = AuthRoleService(db)
    admin_id = session["user"]["id"]
    new_password = request.form.get("new_password")
    try:
        service.admin_reset_password(admin_user_id=admin_id, target_user_id=user_id, new_password=new_password)
        flash("Password reset successfully. The user will be required to change it on next login.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("main.admin_users"))


@bp.post("/admin/rotate-demo-passwords")
@login_required(["Administrator"])
def rotate_demo_passwords():
    import os
    from werkzeug.security import generate_password_hash
    db = session_app_database()
    new_pass = secrets.token_urlsafe(12)
    users_repo = UserRepository(db)
    conn = db.connect()
    try:
        for u in ("admin1", "officer1", "rescue1"):
            users_repo.update_password(u, generate_password_hash(new_pass), must_change=False)
        AuditRepository(db).log(session["user"]["id"], "PASSWORDS_ROTATED", "System", "demo_accounts", "Seeded accounts password rotated")
        flash(f"Privileged demo accounts (admin1, officer1, rescue1) rotated! [SIMULATED ONE-TIME DISPLAY] Temporary password: {new_pass}", "warning")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.get("/reports")
@login_required(["Emergency Officer", "Administrator"])
def reports():
    db = session_app_database()
    overview = ReportingAuditService(db).overview()
    audit = ReportingAuditService(db).audit_log(
        request.args.get("action") or None,
        request.args.get("entity_type") or None,
        limit=request.args.get("limit", 200),
    )
    return render_template("reports.html", overview=overview, audit=audit)


# Lightweight Polling API
@bp.get("/api/updates")
@login_required()
def api_updates():
    db = session_app_database()
    role = session["user"]["role"]
    user_id = session["user"]["id"]
    team_id = session["user"].get("team_id")
    since = request.args.get("since")

    overview = ReportingAuditService(db).overview()
    unread = NotificationRepository(db).count_unread(
        user_id=user_id, team_id=team_id, role=session["user"].get("role_id")
    )

    incidents = IncidentRepository(db).list()

    return jsonify({
        "timestamp": utc_now(),
        "critical_unassigned_count": overview["critical_unassigned_count"],
        "critical_unassigned": overview["critical_unassigned"],
        "unread_notifications": unread,
        "total_incidents": len(incidents),
        "by_status": overview["by_status"],
        "by_severity": overview["by_severity"],
        "by_type": overview["by_type"],
    })


@bp.get("/api/notifications")
@login_required()
def get_notifications():
    db = session_app_database()
    user_id = session["user"]["id"]
    team_id = session["user"].get("team_id")
    role_id = session["user"].get("role_id")
    notifs = NotificationRepository(db).list_for(user_id=user_id, team_id=team_id, role=role_id, limit=20)
    return jsonify(notifs)


@bp.post("/api/notifications/<notification_id>/read")
@login_required()
def mark_notification_read(notification_id):
    db = session_app_database()
    NotificationRepository(db).mark_as_read(notification_id)
    return jsonify({"status": "ok"})


@bp.post("/api/notifications/read-all")
@login_required()
def mark_all_notifications_read():
    db = session_app_database()
    user_id = session["user"]["id"]
    team_id = session["user"].get("team_id")
    role_id = session["user"].get("role_id")
    NotificationRepository(db).mark_all_read(user_id=user_id, team_id=team_id, role=role_id)
    return jsonify({"status": "ok"})
