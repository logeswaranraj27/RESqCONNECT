import hashlib
import math
import secrets
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from app.models.core import Incident
from app.repositories.base import new_id, utc_now
from app.repositories.repositories import (
    AssignmentRepository, AuditRepository, CommunicationRepository,
    IncidentRepository, NotificationRepository, PasswordResetTokenRepository,
    SchedulerLockRepository, TeamRepository, UserRepository,
)


def session_app_database():
    from flask import current_app
    return current_app.extensions["database"]


# In-memory registration rate limiter (IP -> list of timestamp floats)
_REGISTRATION_RATE_LIMIT = {}


class AuthRoleService:
    def __init__(self, database):
        self.db = database
        self.users = UserRepository(database)
        self.audit = AuditRepository(database)
        self.tokens = PasswordResetTokenRepository(database)

    def authenticate(self, identifier, password):
        if not identifier or not password:
            return None, "Please provide username/email and password."

        user = self.users.get_by_username_or_email(identifier.strip())
        if not user:
            return None, "Invalid username or password."

        if not user.get("is_active", 1):
            return None, "This account has been deactivated. Please contact an administrator."

        locked_until = user.get("locked_until")
        if locked_until:
            try:
                locked_time = datetime.fromisoformat(locked_until.replace("Z", "+00:00"))
                if locked_time > datetime.now(timezone.utc):
                    remaining_mins = max(1, int((locked_time - datetime.now(timezone.utc)).total_seconds() / 60) + 1)
                    return None, f"Account temporarily locked due to repeated failed attempts. Please try again in {remaining_mins} minute(s)."
            except (ValueError, TypeError):
                pass

        if not check_password_hash(user["password_hash"], password):
            attempts, is_locked = self.users.record_login_failure(user["id"])
            self.audit.log(user["id"], "LOGIN_FAILED", "User", user["id"], f"Failed attempt #{attempts}")
            if is_locked:
                self.audit.log(user["id"], "ACCOUNT_LOCKED", "User", user["id"], "Account locked for 5 minutes")
                return None, "Account temporarily locked due to repeated failed attempts. Please try again in 5 minutes."
            return None, "Invalid username or password."

        self.users.record_login_success(user["id"])
        self.audit.log(user["id"], "LOGIN_SUCCESS", "User", user["id"], "Successful login")
        return user, None

    def register(self, full_name, username, email, contact_number, password, confirm_password, honeypot="", ip="127.0.0.1"):
        # 1. Honeypot check
        if honeypot and honeypot.strip():
            raise ValueError("Invalid submission")

        # 2. Rate limiting check (max 5 per 60 seconds per IP, skip in testing)
        from flask import current_app
        is_testing = False
        try:
            is_testing = current_app and current_app.config.get("TESTING")
        except Exception:
            pass
        if not is_testing:
            now_ts = time.time()
            attempts = _REGISTRATION_RATE_LIMIT.get(ip, [])
            attempts = [t for t in attempts if now_ts - t < 60]
            if len(attempts) >= 5:
                raise ValueError("Too many registration requests. Please wait a moment before trying again.")
            attempts.append(now_ts)
            _REGISTRATION_RATE_LIMIT[ip] = attempts

        # 3. Input validation
        full_name = (full_name or "").strip()
        username = (username or "").strip()
        email = (email or "").strip().lower()
        contact_number = (contact_number or "").strip()

        if len(full_name) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        if len(username) < 3:
            raise ValueError("Username must be at least 3 characters.")
        if not username.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username can only contain alphanumeric characters, underscores, and dashes.")
        if "@" not in email or "." not in email:
            raise ValueError("Please provide a valid email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if password.lower() == username.lower():
            raise ValueError("Password cannot be identical to your username.")
        if password != confirm_password:
            raise ValueError("Passwords do not match.")

        # 4. Uniqueness check
        if self.users.get_by_username(username):
            raise ValueError("Username is already taken.")
        if self.users.get_by_email(email):
            raise ValueError("Email is already registered.")

        # 5. Enforce Citizen role ALWAYS (never allow elevation via form)
        user_id = new_id("user")
        password_hash = generate_password_hash(password)
        self.users.create(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            display_name=full_name,
            role_id="role-citizen",
            email=email,
            contact_number=contact_number,
            team_id=None,
            is_demo=False,
            must_change=False,
        )
        self.audit.log(user_id, "USER_REGISTERED", "User", user_id, f"Self-registered citizen: {username}")
        return self.users.get(user_id)

    def change_password(self, user_id, current_password, new_password, confirm_password):
        user = self.users.get(user_id)
        if not user:
            raise ValueError("User not found.")
        if not check_password_hash(user["password_hash"], current_password):
            raise ValueError("Current password is incorrect.")
        if len(new_password) < 8:
            raise ValueError("New password must be at least 8 characters.")
        if new_password.lower() == user["username"].lower():
            raise ValueError("Password cannot match your username.")
        if new_password != confirm_password:
            raise ValueError("New passwords do not match.")

        self.users.update_password(user_id, generate_password_hash(new_password), must_change=False)
        self.audit.log(user_id, "PASSWORD_CHANGED", "User", user_id, "Password changed by user")

    def update_profile(self, user_id, display_name, email, contact_number):
        display_name = (display_name or "").strip()
        email = (email or "").strip().lower()
        contact_number = (contact_number or "").strip()

        if len(display_name) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        if "@" not in email or "." not in email:
            raise ValueError("Please provide a valid email address.")

        existing = self.users.get_by_email(email)
        if existing and existing["id"] != user_id:
            raise ValueError("Email address is already in use by another account.")

        self.users.update_profile(user_id, display_name, email, contact_number)
        self.audit.log(user_id, "PROFILE_UPDATED", "User", user_id, "User profile details updated")

    def request_password_reset(self, identifier):
        identifier = (identifier or "").strip()
        user = self.users.get_by_username_or_email(identifier)
        if not user:
            return None, None
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        self.tokens.create_token(user["id"], token_hash, expires_at)
        self.audit.log(user["id"], "PASSWORD_RESET_REQUESTED", "User", user["id"], "Reset token generated")
        return raw_token, user["email"]

    def reset_password_with_token(self, raw_token, new_password, confirm_password):
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_record = self.tokens.find_valid_token(token_hash)
        if not token_record:
            raise ValueError("This password reset link is invalid or has expired.")

        user = self.users.get(token_record["user_id"])
        if not user:
            raise ValueError("User not found.")

        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if new_password.lower() == user["username"].lower():
            raise ValueError("Password cannot match your username.")
        if new_password != confirm_password:
            raise ValueError("Passwords do not match.")

        self.users.update_password(user["id"], generate_password_hash(new_password), must_change=False)
        self.tokens.mark_used(token_record["id"])
        self.audit.log(user["id"], "PASSWORD_RESET_COMPLETED", "User", user["id"], "Password reset via token")

    def update_user_role(self, admin_user_id, target_user_id, new_role_id, team_id=None):
        target = self.users.get(target_user_id)
        if not target:
            raise ValueError("Target user not found.")

        if admin_user_id == target_user_id and new_role_id != "role-admin":
            raise ValueError("Administrators cannot demote their own account.")

        if target["role_id"] == "role-admin" and new_role_id != "role-admin":
            if self.users.count_active_admins() <= 1:
                raise ValueError("Cannot demote the last remaining active administrator.")

        if new_role_id == "role-team" and not team_id:
            raise ValueError("A rescue team must be selected for Rescue Team accounts.")

        clean_team = team_id if new_role_id == "role-team" else None
        self.users.set_role(target_user_id, new_role_id, clean_team)
        self.audit.log(
            admin_user_id, "USER_ROLE_UPDATED", "User", target_user_id,
            f"Role set to {new_role_id}" + (f", linked to team {clean_team}" if clean_team else "")
        )

    def set_user_active(self, admin_user_id, target_user_id, is_active):
        target = self.users.get(target_user_id)
        if not target:
            raise ValueError("Target user not found.")

        if admin_user_id == target_user_id and not is_active:
            raise ValueError("Administrators cannot deactivate their own account.")

        if target["role_id"] == "role-admin" and not is_active:
            if self.users.count_active_admins() <= 1:
                raise ValueError("Cannot deactivate the last remaining active administrator.")

        self.users.set_active(target_user_id, is_active)
        self.audit.log(
            admin_user_id, "USER_STATUS_UPDATED", "User", target_user_id,
            f"Active status set to {is_active}"
        )

    def admin_reset_password(self, admin_user_id, target_user_id, new_password):
        target = self.users.get(target_user_id)
        if not target:
            raise ValueError("Target user not found.")
        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        self.users.update_password(target_user_id, generate_password_hash(new_password), must_change=True)
        self.audit.log(admin_user_id, "USER_PASSWORD_RESET", "User", target_user_id, "Admin reset password")

    def set_session(self, user):
        session.clear()
        session["user"] = {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user.get("role_name") or self.get_role_name(user["role_id"]),
            "role_id": user["role_id"],
            "team_id": user.get("team_id"),
        }
        session["must_change_password"] = bool(user.get("must_change_password", 0))

    def get_role_name(self, role_id):
        from app.repositories.base import BaseRepository
        role = BaseRepository(session_app_database()).fetchone("SELECT name FROM roles WHERE id = ?", (role_id,))
        return role["name"] if role else "Citizen"


class IncidentService:
    def __init__(self, database):
        self.db = database
        self.incidents = IncidentRepository(database)
        self.audit = AuditRepository(database)
        self.notifications = NotificationRepository(database)
        self.teams = TeamRepository(database)
        self.assignments = AssignmentRepository(database)

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

    def find_duplicate_id(self, incident):
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
                return existing["id"]
        return None

    def duplicate_exists(self, incident):
        return self.find_duplicate_id(incident) is not None

    def create(self, incident_type, description, address, latitude, longitude, severity, reporter_id):
        existing_dup_id = self.find_duplicate_id(Incident(
            id="temp",
            incident_type=incident_type,
            description=description,
            address=address,
            latitude=latitude,
            longitude=longitude,
            severity=severity,
            status="Received",
            reporter_id=reporter_id,
            created_at=utc_now(),
        ))

        incident = Incident(
            id=new_id("incident"),
            incident_type=incident_type,
            description=description,
            address=address,
            latitude=latitude,
            longitude=longitude,
            severity=severity,
            reported_severity=severity,
            effective_severity=severity,
            status="Received",
            reporter_id=reporter_id,
            duplicate_flag=bool(existing_dup_id),
            duplicate_of=existing_dup_id,
            suspicious_flag=self.suspicious(description),
            is_demo=False,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.incidents.create(incident)
        self.audit.log(
            reporter_id, "INCIDENT_REPORTED", "Incident", incident.id,
            f"Report received ({severity})" + (f"; duplicate of {existing_dup_id}" if existing_dup_id else "") +
            ("; suspicious flag" if incident.suspicious_flag else "")
        )
        # Notify officers
        self.notifications.create(
            title=f"New Incident: {incident.id}",
            message=f"{incident_type} reported at {address} ({severity} severity)",
            role="role-officer",
            incident_id=incident.id,
        )
        return incident

    def update_severity(self, incident_id, officer_id, new_severity, reason=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found.")
        current_effective = incident.get("effective_severity") or incident.get("severity")
        if new_severity != current_effective and not (reason and reason.strip()):
            raise ValueError("A mandatory reason must be provided when modifying the incident severity.")
        
        self.incidents.update_effective_severity(incident_id, new_severity, reason.strip() if reason else None)
        self.audit.log(
            officer_id, "SEVERITY_UPDATED", "Incident", incident_id,
            f"Effective severity updated from {current_effective} to {new_severity}. Reason: {reason}"
        )

    def resolve_flags(self, incident_id, officer_id, action, notes="", duplicate_of=None):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found.")

        if action == "genuine":
            self.incidents.update_flags(incident_id, False, False, duplicate_of=None)
            VerificationService(self.db).verify(incident_id, officer_id, "Officer review", "Verified", f"Flag cleared as genuine: {notes}")
            self.audit.log(officer_id, "FLAG_RESOLVED_GENUINE", "Incident", incident_id, f"Marked genuine: {notes}")
        elif action == "duplicate":
            if not duplicate_of:
                raise ValueError("Original incident ID must be specified for duplicate linking.")
            orig = self.incidents.get(duplicate_of)
            if not orig:
                raise ValueError("Original incident not found.")
            self.incidents.update_flags(incident_id, True, False, duplicate_of=duplicate_of)
            self.incidents.cancel_incident(incident_id, f"Duplicate of {duplicate_of}: {notes}")
            self.audit.log(officer_id, "FLAG_RESOLVED_DUPLICATE", "Incident", incident_id, f"Linked to duplicate {duplicate_of}: {notes}")
        elif action == "false_report":
            if not (notes and notes.strip()):
                raise ValueError("A specific reason is required to mark an incident as a false report.")
            self.incidents.update_flags(incident_id, False, True, duplicate_of=None)
            self.incidents.cancel_incident(incident_id, f"Marked as false report by officer: {notes}")
            self.audit.log(officer_id, "FLAG_RESOLVED_FALSE_REPORT", "Incident", incident_id, f"False report reason: {notes}")
        else:
            raise ValueError(f"Invalid flag resolution action: {action}")

    def cancel(self, incident_id, actor_id, reason):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found.")
        if incident["status"] in ("Closed", "Cancelled"):
            raise ValueError(f"Cannot cancel incident that is already {incident['status']}.")
        if not (reason and reason.strip()):
            raise ValueError("A cancellation reason is required.")

        # If there is an active assignment, cancel it and release the team
        active_assignment = self.assignments.get_active_for_incident(incident_id)
        if active_assignment:
            self.assignments.cancel_assignment(active_assignment["id"])
            self.teams.set_availability(active_assignment["team_id"], "Available")
            self.notifications.create(
                title=f"Incident Cancelled: {incident_id}",
                message=f"Incident {incident_id} has been cancelled. Stand down.",
                team_id=active_assignment["team_id"],
                incident_id=incident_id,
            )

        self.incidents.cancel_incident(incident_id, reason.strip())
        self.audit.log(actor_id, "INCIDENT_CANCELLED", "Incident", incident_id, f"Reason: {reason}")

    def close(self, incident_id, actor_id, notes=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found.")
        if incident["status"] != "Resolved":
            raise ValueError("Only Resolved incidents can be Closed.")

        self.incidents.close_incident(incident_id, notes)
        self.audit.log(actor_id, "INCIDENT_CLOSED", "Incident", incident_id, f"Closed with notes: {notes}")

    def reopen(self, incident_id, actor_id, reason):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found.")
        if incident["status"] not in ("Resolved", "Closed", "Cancelled"):
            raise ValueError("Only Resolved, Closed, or Cancelled incidents can be reopened.")
        if not (reason and reason.strip()):
            raise ValueError("A reason is required to reopen an incident.")

        new_status = "Under Review"
        self.incidents.reopen_incident(incident_id, new_status, reason.strip())
        self.audit.log(actor_id, "INCIDENT_REOPENED", "Incident", incident_id, f"Reopened to {new_status}. Reason: {reason}")


class VerificationService:
    ALLOWED = {
        "Received": ("Under Review", "Verification Required", "Duplicate Review", "Cancelled"),
        "Under Review": ("Verified", "Verification Required", "Duplicate Review", "Escalated", "Cancelled"),
        "Verification Required": ("Under Review", "Verified", "Cancelled"),
        "Duplicate Review": ("Under Review", "Verified", "Cancelled"),
        "Verified": ("Prioritized", "Escalated", "Cancelled"),
        "Prioritized": ("Assigned", "Escalated", "Cancelled", "Verified"),
        "Assigned": ("In Progress", "Escalated", "Prioritized", "Cancelled"),
        "In Progress": ("Resolved", "Escalated", "Cancelled"),
        "Escalated": ("Assigned", "Prioritized", "Verified", "Cancelled"),
        "Unassigned": ("Prioritized", "Assigned", "Cancelled"),
        "Resolved": ("Closed", "Under Review"),
        "Closed": ("Under Review",),
        "Cancelled": ("Under Review",),
    }

    def __init__(self, database):
        self.db = database
        self.incidents = IncidentRepository(database)
        self.audit = AuditRepository(database)

    def transition(self, incident_id, actor_id, new_status, notes=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found")
        allowed = self.ALLOWED.get(incident["status"], ())
        if new_status not in allowed:
            raise ValueError(f"Illegal transition: {incident['status']} -> {new_status}")
        self.incidents.update_status(incident_id, new_status, notes=notes if notes else None)
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
        self.notifications = NotificationRepository(database)

    def recommend_priority(self, incident):
        eff_sev = incident.get("effective_severity") or incident.get("severity", "Medium")
        severity_weight = {"Low": 15, "Medium": 35, "High": 65, "Critical": 90}.get(eff_sev, 35)
        type_weight = {"Fire": 15, "Flood": 12, "Earthquake": 18, "Other": 8}.get(incident.get("incident_type"), 8)
        score = min(100, severity_weight + type_weight)
        level = "Critical" if score >= 85 else "High" if score >= 60 else "Medium" if score >= 35 else "Low"
        return score, level

    def prioritize(self, incident_id, actor_id, confirmed_severity=None, reason=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found")
        if incident["status"] not in ("Verified", "Under Review"):
            raise ValueError(f"Incident cannot be prioritized from status '{incident['status']}'")

        if confirmed_severity and confirmed_severity != incident.get("effective_severity"):
            IncidentService(self.db).update_severity(incident_id, actor_id, confirmed_severity, reason)
            incident = self.incidents.get(incident_id)

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
        if incident["status"] not in ("Prioritized", "Unassigned", "Escalated", "Verified"):
            raise ValueError(f"Incident in status '{incident['status']}' cannot be assigned.")

        # Concurrency safety: check if another officer has already assigned this incident
        active_assignment = self.assignments.get_active_for_incident(incident_id)
        if active_assignment:
            raise ValueError("Already handled: This incident already has an active team assignment.")

        # Check if team is already committed to an active assignment
        team = self.teams.get(team_id)
        if not team:
            raise ValueError("Rescue team not found")
        team_active = self.assignments.get_active_for_team(team_id)
        if team_active:
            raise ValueError(f"Rescue team '{team['name']}' is already committed to incident {team_active['incident_id']}.")

        # Optimistic concurrency locking on incident
        inc_version = incident.get("version", 1)
        if not self.incidents.increment_version(incident_id, inc_version):
            raise ValueError("Already handled by another officer: This incident was just modified or assigned.")

        # Optimistic concurrency locking on team
        team_version = team.get("version", 1)
        if not self.teams.increment_version(team_id, team_version):
            raise ValueError(f"Already handled: Team '{team['name']}' was just assigned by another officer.")

        assignment_id = new_id("assignment")
        self.assignments.create_with_id(
            assignment_id, incident_id, team_id, "PendingAcknowledgement", officer_id,
            revise_reason or "Officer approved recommendation", is_demo=False,
        )
        self.incidents.update_status(incident_id, "Assigned")
        self.teams.set_availability(team_id, "Committed")

        # Communication record & in-app notification
        CommunicationRepository(self.db).create(
            incident_id, assignment_id, team_id, "InApp", "Delivered", "In-app assignment alert",
        )
        self.notifications.create(
            title=f"New Assignment: {incident['incident_type']}",
            message=f"Dispatched to incident {incident_id} at {incident['address']}",
            team_id=team_id,
            incident_id=incident_id,
        )
        self.audit.log(officer_id, "ASSIGNMENT_APPROVED", "Assignment", assignment_id,
                       f"Incident {incident_id} assigned to {team_id}")
        return assignment_id

    def reassign_team(self, incident_id, new_team_id, officer_id, reason=""):
        incident = self.incidents.get(incident_id)
        if not incident:
            raise ValueError("Incident not found")

        current_assignment = self.assignments.get_active_for_incident(incident_id)
        if not current_assignment:
            # If no active assignment, regular assignment
            return self.approve_assignment(incident_id, new_team_id, officer_id, reason)

        old_team_id = current_assignment["team_id"]
        if old_team_id == new_team_id:
            raise ValueError("The selected team is already assigned to this incident.")

        # Check new team
        new_team = self.teams.get(new_team_id)
        if not new_team:
            raise ValueError("New rescue team not found")
        new_team_active = self.assignments.get_active_for_team(new_team_id)
        if new_team_active:
            raise ValueError(f"Rescue team '{new_team['name']}' is already committed to another incident.")

        # 1. Release previous team and cancel old assignment
        self.assignments.cancel_assignment(current_assignment["id"])
        self.teams.set_availability(old_team_id, "Available")
        self.notifications.create(
            title=f"Assignment Reassigned: {incident_id}",
            message=f"Incident {incident_id} reassigned. Your team is released to Available.",
            team_id=old_team_id,
            incident_id=incident_id,
        )

        # 2. Assign new team
        new_assignment_id = new_id("assignment")
        self.assignments.create_with_id(
            new_assignment_id, incident_id, new_team_id, "PendingAcknowledgement", officer_id,
            reason or f"Reassigned from {old_team_id}", is_demo=False,
        )
        self.incidents.update_status(incident_id, "Assigned")
        self.teams.set_availability(new_team_id, "Committed")
        self.notifications.create(
            title=f"Urgent Reassignment: {incident_id}",
            message=f"You have been assigned to incident {incident_id} at {incident['address']}",
            team_id=new_team_id,
            incident_id=incident_id,
        )
        self.audit.log(
            officer_id, "TEAM_REASSIGNED", "Assignment", new_assignment_id,
            f"Incident {incident_id} reassigned from {old_team_id} to {new_team_id}. Reason: {reason}"
        )
        return new_assignment_id

    def cancel_assignment(self, assignment_id, officer_id, reason=""):
        assignment = self.assignments.get(assignment_id)
        if not assignment:
            raise ValueError("Assignment not found")
        if assignment["status"] in ("Completed", "Cancelled"):
            raise ValueError(f"Assignment is already {assignment['status']}.")

        self.assignments.cancel_assignment(assignment_id)
        self.teams.set_availability(assignment["team_id"], "Available")
        self.incidents.update_status(assignment["incident_id"], "Unassigned", notes=f"Assignment cancelled: {reason}")
        self.notifications.create(
            title=f"Assignment Cancelled: {assignment['incident_id']}",
            message=f"Assignment {assignment_id} has been cancelled by officer.",
            team_id=assignment["team_id"],
            incident_id=assignment["incident_id"],
        )
        self.audit.log(
            officer_id, "ASSIGNMENT_CANCELLED", "Assignment", assignment_id,
            f"Cancelled assignment for incident {assignment['incident_id']}. Reason: {reason}"
        )


class AlertCoordinationService:
    def __init__(self, database):
        self.db = database
        self.assignments = AssignmentRepository(database)
        self.communication = CommunicationRepository(database)
        self.audit = AuditRepository(database)
        self.incidents = IncidentRepository(database)
        self.teams = TeamRepository(database)
        self.notifications = NotificationRepository(database)
        self.locks = SchedulerLockRepository(database)

    def escalate_expired(self, timeout_seconds):
        worker_id = f"worker-{secrets.token_hex(4)}"
        # Acquire lock to prevent duplicate escalations across concurrent threads/workers
        if not self.locks.acquire_lock("acknowledgement_escalation", worker_id, duration_seconds=15):
            return 0

        try:
            now = datetime.now(timezone.utc)
            escalated_count = 0
            for assignment in self.assignments.fetchall(
                "SELECT assignments.*, incidents.status AS incident_status FROM assignments "
                "JOIN incidents ON incidents.id = assignments.incident_id "
                "WHERE assignments.status IN ('PendingAcknowledgement', 'PendingApproval')"
            ):
                try:
                    created = datetime.fromisoformat(assignment["created_at"].replace("Z", "+00:00"))
                    elapsed = (now - created).total_seconds()
                except (ValueError, TypeError):
                    continue
                if elapsed >= timeout_seconds and not self.communication.escalation_exists(assignment["id"]):
                    self.assignments.update_status(assignment["id"], "Escalated")
                    self.incidents.update_status(assignment["incident_id"], "Escalated")
                    self.communication.create(
                        assignment["incident_id"], assignment["id"], None, "InApp",
                        "Escalated", "Acknowledgement timeout: manual officer follow-up required",
                        escalated=True,
                    )
                    self.notifications.create(
                        title=f"ESCALATION: Assignment Timeout {assignment['id']}",
                        message=f"Team {assignment['team_id']} failed to acknowledge incident {assignment['incident_id']} within {timeout_seconds}s.",
                        role="role-officer",
                        incident_id=assignment["incident_id"],
                    )
                    self.audit.log(None, "ACKNOWLEDGEMENT_ESCALATED", "Assignment", assignment["id"],
                                   f"Timeout {timeout_seconds}s exceeded")
                    escalated_count += 1
            return escalated_count
        finally:
            self.locks.release_lock("acknowledgement_escalation", worker_id)

    def acknowledge(self, assignment_id, team_id, actor_id):
        assignment = self.assignments.fetchone("SELECT * FROM assignments WHERE id = ? AND team_id = ?", (assignment_id, team_id))
        if not assignment:
            raise PermissionError("Assignment not found for this rescue team")
        if assignment["status"] == "Acknowledged":
            return
        if assignment["status"] in ("Completed", "Cancelled"):
            raise ValueError(f"Cannot acknowledge assignment in status {assignment['status']}.")

        self.assignments.update_status(assignment_id, "Acknowledged", "acknowledged_at")
        self.incidents.update_status(assignment["incident_id"], "In Progress")
        self.notifications.create(
            title=f"Assignment Acknowledged: {assignment['incident_id']}",
            message=f"Team {team_id} acknowledged incident {assignment['incident_id']}.",
            role="role-officer",
            incident_id=assignment["incident_id"],
        )
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
        self.incidents.update_status(assignment["incident_id"], incident_status)
        if status == "Completed":
            self.teams.set_availability(team_id, "Available")

        self.notifications.create(
            title=f"Team Status: {status} ({assignment['incident_id']})",
            message=f"Team {team_id} updated status to {status} for incident {assignment['incident_id']}.",
            role="role-officer",
            incident_id=assignment["incident_id"],
        )
        self.audit.log(actor_id, "TEAM_STATUS_UPDATED", "Assignment", assignment_id, status)


class ReportingAuditService:
    def __init__(self, database):
        self.db = database
        self.audit = AuditRepository(database)
        self.incidents = IncidentRepository(database)
        self.assignments = AssignmentRepository(database)

    def audit_log(self, action=None, entity_type=None, limit=300):
        return self.audit.list(action=action, entity_type=entity_type, limit=limit)

    def critical_unassigned_incidents(self):
        """Unified definition:
        effective_severity == 'Critical' AND status NOT IN ('Resolved', 'Closed', 'Cancelled')
        AND no active assignment ('PendingApproval', 'PendingAcknowledgement', 'Acknowledged', 'Dispatched', 'On Scene').
        """
        active_assignments = self.assignments.fetchall(
            "SELECT incident_id FROM assignments WHERE status IN ('PendingApproval', 'PendingAcknowledgement', 'Acknowledged', 'Dispatched', 'On Scene')"
        )
        active_incident_ids = {a["incident_id"] for a in active_assignments}
        all_incidents = self.incidents.list()

        results = []
        for inc in all_incidents:
            eff_sev = inc.get("effective_severity") or inc.get("severity")
            if eff_sev == "Critical" and inc["status"] not in ("Resolved", "Closed", "Cancelled"):
                if inc["id"] not in active_incident_ids:
                    results.append(inc)
        return results

    def overview(self):
        incidents = self.incidents.list()
        critical_unassigned = self.critical_unassigned_incidents()
        return {
            "total": len(incidents),
            "by_status": self._count(incidents, "status"),
            "by_type": self._count(incidents, "incident_type"),
            "by_severity": self._count_effective_severity(incidents),
            "critical_unassigned": critical_unassigned,
            "critical_unassigned_count": len(critical_unassigned),
        }

    @staticmethod
    def _count(records, field):
        result = {}
        for record in records:
            value = record[field]
            result[value] = result.get(value, 0) + 1
        return result

    @staticmethod
    def _count_effective_severity(records):
        result = {}
        for record in records:
            value = record.get("effective_severity") or record.get("severity")
            result[value] = result.get(value, 0) + 1
        return result
