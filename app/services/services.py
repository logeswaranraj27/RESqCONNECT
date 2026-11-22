import math
from datetime import datetime, timezone
from difflib import SequenceMatcher

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from app.repositories.base import new_id, utc_now
from app.repositories.repositories import (
    AssignmentRepository, AuditRepository, CommunicationRepository,
    IncidentRepository, TeamRepository, UserRepository,
)


class AuthRoleService:
    def __init__(self, database):
        self.users = UserRepository(database)

    def authenticate(self, username, password):
        user = self.users.get_by_username(username.strip())
        if not user or not check_password_hash(user["password_hash"], password):
            return None
        return user

    def set_session(self, user):
        session.clear()
        session["user"] = {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": self.get_role_name(user["role_id"]),
        }

    def get_role_name(self, role_id):
        from app.repositories.base import BaseRepository
        role = BaseRepository(session_app_database()).fetchone("SELECT name FROM roles WHERE id = ?", (role_id,))
        return role["name"] if role else "Citizen"


def session_app_database():
    from flask import current_app
    return current_app.extensions["database"]


class IncidentService:
    def __init__(self, database):
        self.db = database
        self.incidents = IncidentRepository(database)
        self.audit = AuditRepository(database)

    @staticmethod
    def suspicious(description):
        terms = ("hoax", "prank", "fake", "test only", "joke")
        text = description.lower()
        return any(term in text for term in terms)

    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        radius = 6371
        p1, p2 = math.radians(lat1), math.radians(lat2)
        delta = math.radians(lon2 - lon1)
        value = math.sin((p2-p1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(delta/2)**2
        return 2 * radius * math.asin(min(1, math.sqrt(value)))

    def duplicate_exists(self, incident):
        for existing in self.incidents.list():
            if existing["incident_type"] != incident.incident_type:
                continue
            distance = self.haversine(incident.latitude, incident.longitude, existing["latitude"], existing["longitude"])
            similarity = SequenceMatcher(None, incident.description.lower(), existing["description"].lower()).ratio()
            try:
                existing_time = datetime.fromisoformat(existing["created_at"].replace("Z", "+00:00"))
                now = datetime.fromisoformat(incident.created_at.replace("Z", "+00:00"))
                minutes = abs((now - existing_time).total_seconds()) / 60
            except (ValueError, TypeError):
                minutes = 99999
            if distance <= 1.0 and minutes <= 180 and similarity >= 0.55:
                return True
        return False

    def create(self, incident_type, description, address, latitude, longitude, severity, reporter_id):
        incident = __import__("app.models.core", fromlist=["Incident"]).Incident(
            id=new_id("incident"),
            incident_type=incident_type,
            description=description,
            address=address,
            latitude=latitude,
            longitude=longitude,
            severity=severity,
            status="Received",
            reporter_id=reporter_id,
        )
        incident.duplicate_flag = self.duplicate_exists(incident)
        incident.suspicious_flag = self.suspicious(description)
        incident.created_at = utc_now()
        incident.updated_at = incident.created_at
        self.incidents.create(incident)
        self.audit.log(reporter_id, "INCIDENT_REPORTED", "Incident", incident.id,
                       "Demo report received" + ("; duplicate flag" if incident.duplicate_flag else "") +
                       ("; suspicious flag" if incident.suspicious_flag else ""))
        return incident


class VerificationService:
    ALLOWED = {
        "Received": ("Under Review", "Verification Required", "Duplicate Review", "Cancelled"),
        "Under Review": ("Verified", "Verification Required", "Duplicate Review", "Escalated", "Cancelled"),
        "Verification Required": ("Under Review", "Verified", "Cancelled"),
        "Duplicate Review": ("Under Review", "Verified", "Cancelled"),
    }

    def __init__(self, database):
        self.incidents = IncidentRepository(database)
        self.audit = AuditRepository(database)

    def transition(self, incident_id, actor_id, new_status, notes=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found")
        allowed = self.ALLOWED.get(incident["status"], ())
        if new_status not in allowed:
            raise ValueError(f"Illegal transition: {incident['status']} -> {new_status}")
        self.incidents.update_status(incident_id, new_status)
        self.audit.log(actor_id, "STATUS_TRANSITION", "Incident", incident_id,
                       f"{incident['status']} -> {new_status}" + (f"; {notes}" if notes else ""))
        return True

    def verify(self, incident_id, reviewer_id, method, result, notes=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found")
        current_status = incident["status"]
        if current_status == "Verified":
            raise ValueError("Incident is already verified")
        if current_status == "Received":
            self.transition(incident_id, reviewer_id, "Under Review")
            current_status = "Under Review"

        if current_status not in ("Under Review", "Verification Required", "Duplicate Review"):
            raise ValueError(f"Cannot verify incident in status '{current_status}'")

        if result == "False Report":
            self.transition(incident_id, reviewer_id, "Cancelled", f"Marked as false report: {notes}")
        elif result == "Duplicate":
            self.transition(incident_id, reviewer_id, "Duplicate Review", notes)
        elif result == "Needs Field Check":
            self.transition(incident_id, reviewer_id, "Verification Required", notes)
        else:
            self.transition(incident_id, reviewer_id, "Verified", notes)

        connection = self.incidents.database.connect()
        try:
            cursor = connection.cursor()
            from app.repositories.base import new_id
            cursor.execute(
                self.incidents.database.placeholder_sql(
                    "INSERT INTO verification_records (id, incident_id, reviewer_id, method, result, notes, reviewed_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, 0)"
                ),
                (new_id("verify"), incident_id, reviewer_id, method, result, notes, utc_now()),
            )
            connection.commit()
        finally:
            connection.close()
        self.audit.log(reviewer_id, "INCIDENT_VERIFIED", "Incident", incident_id,
                       f"Verification method: {method}; result: {result}")


class PrioritizationAllocationService:
    def __init__(self, database):
        self.db = database
        self.incidents = IncidentRepository(database)
        self.teams = TeamRepository(database)
        self.assignments = AssignmentRepository(database)
        self.audit = AuditRepository(database)

    def recommend_priority(self, incident):
        severity = {"Low": 15, "Medium": 35, "High": 65, "Critical": 90}.get(incident["severity"], 30)
        incident_type = {"Fire": 15, "Flood": 12, "Earthquake": 18, "Other": 8}.get(incident["incident_type"], 8)
        score = min(100, severity + incident_type)
        level = "Critical" if score >= 85 else "High" if score >= 60 else "Medium" if score >= 35 else "Low"
        return score, level

    def prioritize(self, incident_id, actor_id):
        incident = self.incidents.get(incident_id)
        if incident["status"] not in ("Verified",):
            raise ValueError("Incident must be Verified before prioritization")
        score, level = self.recommend_priority(incident)
        self.incidents.update_priority(incident_id, score, level)
        self.incidents.update_status(incident_id, "Prioritized")
        self.audit.log(actor_id, "PRIORITY_RECOMMENDED", "Incident", incident_id,
                       f"Recommended score {score}, level {level}")

    def recommend_team(self, incident):
        capability_map = {"Flood": "FloodRescue", "Fire": "FireRescue", "Earthquake": "HeavyRescue", "Other": "General"}
        required = capability_map.get(incident["incident_type"], "General")
        candidates = []
        for team in self.teams.list():
            capabilities = team["capabilities"].split(",")
            capability_match = required in capabilities
            availability = 100 if team["availability"] == "Available" else 40 if team["availability"] == "Limited" else 0
            score = availability + (60 if capability_match else 10)
            candidates.append((score, team, capability_match))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if not candidates:
            return None, "No rescue team is available"
        score, team, capability_match = candidates[0]
        reason = f"{required} capability match" if capability_match else "General availability fallback"
        return team, f"{reason}; availability={team['availability']}; score={score}"

    def approve_assignment(self, incident_id, team_id, officer_id, revise_reason=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found")
        if incident["status"] != "Prioritized":
            raise ValueError("Incident must be Prioritized before assignment")
        team = self.teams.get(team_id)
        if not team:
            raise ValueError("Rescue team not found")
        assignment_id = new_id("assignment")
        self.assignments.create_with_id(assignment_id, incident_id, team_id, "PendingAcknowledgement", officer_id,
                                        revise_reason or "Officer approved recommendation")
        self.incidents.update_status(incident_id, "Assigned")
        self.teams.set_availability(team_id, "Committed")
        CommunicationRepository(self.db).create(
            incident_id, assignment_id, team_id, "InApp",
            "Delivered", "Demo in-app assignment alert",
        )
        self.audit.log(officer_id, "ASSIGNMENT_APPROVED", "Assignment", assignment_id,
                       f"Incident {incident_id} assigned to {team_id}")
        return assignment_id


class AlertCoordinationService:
    def __init__(self, database):
        self.db = database
        self.assignments = AssignmentRepository(database)
        self.communication = CommunicationRepository(database)
        self.audit = AuditRepository(database)

    def escalate_expired(self, timeout_seconds):
        now = datetime.now(timezone.utc)
        escalated_count = 0
        for assignment in self.assignments.fetchall(
            "SELECT assignments.*, incidents.status AS incident_status FROM assignments JOIN incidents ON incidents.id = assignments.incident_id "
            "WHERE assignments.status IN ('PendingAcknowledgement', 'PendingApproval')"
        ):
            try:
                created = datetime.fromisoformat(assignment["created_at"].replace("Z", "+00:00"))
                elapsed = (now - created).total_seconds()
            except (ValueError, TypeError):
                continue
            if elapsed >= timeout_seconds and not self.communication.escalation_exists(assignment["id"]):
                self.assignments.update_status(assignment["id"], "Escalated")
                IncidentRepository(self.db).update_status(assignment["incident_id"], "Escalated")
                self.communication.create(
                    assignment["incident_id"], assignment["id"], None, "InApp",
                    "Escalated", "Acknowledgement timeout: manual officer follow-up required",
                    escalated=True,
                )
                self.audit.log(None, "ACKNOWLEDGEMENT_ESCALATED", "Assignment", assignment["id"],
                               f"Timeout {timeout_seconds} seconds exceeded")
                escalated_count += 1
        return escalated_count

    def acknowledge(self, assignment_id, team_id, actor_id):
        assignment = self.assignments.fetchone("SELECT * FROM assignments WHERE id = ? AND team_id = ?", (assignment_id, team_id))
        if not assignment:
            raise PermissionError("Assignment not found for this rescue team")
        self.assignments.update_status(assignment_id, "Acknowledged", "acknowledged_at")
        IncidentRepository(self.db).update_status(assignment["incident_id"], "In Progress")
        self.audit.log(actor_id, "ASSIGNMENT_ACKNOWLEDGED", "Assignment", assignment_id, "Team acknowledged assignment")

    def team_status(self, assignment_id, team_id, actor_id, status):
        if status not in ("Dispatched", "On Scene", "Completed"):
            raise ValueError("Invalid team status")
        assignment = self.assignments.fetchone("SELECT * FROM assignments WHERE id = ? AND team_id = ?", (assignment_id, team_id))
        if not assignment:
            raise PermissionError("Assignment not found for this rescue team")
        mapping = {"Dispatched": ("Dispatched", "dispatched_at", "In Progress"),
                   "On Scene": ("On Scene", "on_scene_at", "In Progress"),
                   "Completed": ("Completed", "completed_at", "Resolved")}
        assignment_status, field, incident_status = mapping[status]
        self.assignments.update_status(assignment_id, assignment_status, field)
        IncidentRepository(self.db).update_status(assignment["incident_id"], incident_status)
        if status == "Completed":
            TeamRepository(self.db).set_availability(team_id, "Available")
        self.audit.log(actor_id, "TEAM_STATUS_UPDATED", "Assignment", assignment_id, status)


class ReportingAuditService:
    def __init__(self, database):
        self.db = database
        self.audit = AuditRepository(database)
        self.incidents = IncidentRepository(database)

    def audit_log(self, action=None, entity_type=None):
        return self.audit.list(action=action, entity_type=entity_type)

    def overview(self):
        incidents = self.incidents.list()
        return {
            "total": len(incidents),
            "by_status": self._count(incidents, "status"),
            "by_type": self._count(incidents, "incident_type"),
            "by_severity": self._count(incidents, "severity"),
            "critical_unassigned": [i for i in incidents if i["severity"] == "Critical" and i["status"] in ("Verified", "Prioritized", "Unassigned", "Escalated")],
        }

    @staticmethod
    def _count(records, field):
        result = {}
        for record in records:
            value = record[field]
            result[value] = result.get(value, 0) + 1
        return result
