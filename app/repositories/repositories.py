from datetime import datetime, timezone, timedelta
from app.repositories.base import BaseRepository, new_id, utc_now


class UserRepository(BaseRepository):
    def get_by_username_or_email(self, identifier):
        return self.fetchone(
            """SELECT users.*, roles.name AS role_name
            FROM users
            JOIN roles ON roles.id = users.role_id
            WHERE LOWER(users.username) = LOWER(?) OR LOWER(users.email) = LOWER(?)""",
            (identifier, identifier),
        )

    def get_by_username(self, username):
        return self.fetchone(
            """SELECT users.*, roles.name AS role_name
            FROM users
            JOIN roles ON roles.id = users.role_id
            WHERE LOWER(users.username) = LOWER(?)""",
            (username,),
        )

    def get_by_email(self, email):
        return self.fetchone(
            """SELECT users.*, roles.name AS role_name
            FROM users
            JOIN roles ON roles.id = users.role_id
            WHERE LOWER(users.email) = LOWER(?)""",
            (email,),
        )

    def get(self, user_id):
        return self.fetchone(
            """SELECT users.*, roles.name AS role_name
            FROM users
            JOIN roles ON roles.id = users.role_id
            WHERE users.id = ?""",
            (user_id,),
        )

    def list_users(self, role_id=None, search=None):
        sql = """SELECT users.*, roles.name AS role_name, rescue_teams.name AS team_name
                 FROM users
                 JOIN roles ON roles.id = users.role_id
                 LEFT JOIN rescue_teams ON rescue_teams.id = users.team_id
                 WHERE 1=1"""
        params = []
        if role_id:
            sql += " AND users.role_id = ?"
            params.append(role_id)
        if search:
            search_pattern = f"%{search.strip().lower()}%"
            sql += " AND (LOWER(users.username) LIKE ? OR LOWER(users.display_name) LIKE ? OR LOWER(users.email) LIKE ?)"
            params.extend([search_pattern, search_pattern, search_pattern])
        sql += " ORDER BY users.display_name"
        return self.fetchall(sql, tuple(params))

    def create(self, user_id, username, password_hash, display_name, role_id, email="", contact_number="", team_id=None, is_demo=False, must_change=False):
        now = utc_now()
        return self.execute(
            """INSERT INTO users
            (id, username, password_hash, display_name, role_id, email, contact_number, team_id, is_active, failed_login_count, locked_until, must_change_password, created_at, is_demo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, ?, ?, ?)""",
            (user_id, username, password_hash, display_name, role_id, email, contact_number, team_id, 1 if must_change else 0, now, 1 if is_demo else 0),
        )

    def set_role(self, user_id, role_id, team_id=None):
        return self.execute("UPDATE users SET role_id = ?, team_id = ? WHERE id = ?", (role_id, team_id, user_id))

    def set_active(self, user_id, is_active):
        return self.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))

    def update_profile(self, user_id, display_name, email, contact_number):
        return self.execute(
            "UPDATE users SET display_name = ?, email = ?, contact_number = ? WHERE id = ?",
            (display_name, email, contact_number, user_id),
        )

    def update_password(self, user_id, password_hash, must_change=False):
        return self.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ?, failed_login_count = 0, locked_until = NULL WHERE id = ?",
            (password_hash, 1 if must_change else 0, user_id),
        )

    def record_login_failure(self, user_id, max_attempts=5, lock_duration_minutes=5):
        user = self.get(user_id)
        if not user:
            return 0, False
        attempts = (user.get("failed_login_count") or 0) + 1
        locked_until = None
        is_locked = False
        if attempts >= max_attempts:
            locked_until = (datetime.now(timezone.utc) + timedelta(minutes=lock_duration_minutes)).isoformat()
            is_locked = True
        self.execute(
            "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
            (attempts, locked_until, user_id),
        )
        return attempts, is_locked

    def record_login_success(self, user_id):
        return self.execute(
            "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?",
            (user_id,),
        )

    def count_active_admins(self):
        row = self.fetchone("SELECT COUNT(*) AS total FROM users WHERE role_id = 'role-admin' AND is_active = 1")
        return row["total"] if row else 0


class IncidentRepository(BaseRepository):
    def create(self, incident):
        now = utc_now()
        reported_sev = incident.reported_severity or incident.severity
        effective_sev = incident.effective_severity or incident.severity
        return self.execute(
            """INSERT INTO incidents
            (id, incident_type, description, address, latitude, longitude, severity, reported_severity, effective_severity,
             status, reporter_id, priority_score, priority_level, duplicate_flag, suspicious_flag, duplicate_of,
             cancellation_reason, officer_notes, version, is_demo, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (incident.id, incident.incident_type, incident.description, incident.address, incident.latitude,
             incident.longitude, effective_sev, reported_sev, effective_sev, incident.status, incident.reporter_id,
             incident.priority_score, incident.priority_level, 1 if incident.duplicate_flag else 0,
             1 if incident.suspicious_flag else 0, incident.duplicate_of, incident.cancellation_reason,
             incident.officer_notes, 1 if incident.is_demo else 0, now, now),
        )

    def get(self, incident_id):
        return self.fetchone("SELECT * FROM incidents WHERE id = ?", (incident_id,))

    def list(self, status=None, reporter_id=None):
        sql = "SELECT * FROM incidents WHERE 1=1"
        params = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if reporter_id:
            sql += " AND reporter_id = ?"
            params.append(reporter_id)
        sql += " ORDER BY created_at DESC"
        return self.fetchall(sql, tuple(params))

    def update_status(self, incident_id, status, notes=None):
        sql = "UPDATE incidents SET status = ?, updated_at = ?"
        params = [status, utc_now()]
        if notes is not None:
            sql += ", officer_notes = ?"
            params.append(notes)
        sql += " WHERE id = ?"
        params.append(incident_id)
        return self.execute(sql, tuple(params))

    def update_effective_severity(self, incident_id, new_severity, reason=None):
        sql = "UPDATE incidents SET effective_severity = ?, severity = ?, updated_at = ?"
        params = [new_severity, new_severity, utc_now()]
        if reason:
            sql += ", officer_notes = ?"
            params.append(reason)
        sql += " WHERE id = ?"
        params.append(incident_id)
        return self.execute(sql, tuple(params))

    def update_priority(self, incident_id, score, level):
        return self.execute(
            "UPDATE incidents SET priority_score = ?, priority_level = ?, updated_at = ? WHERE id = ?",
            (score, level, utc_now(), incident_id),
        )

    def update_flags(self, incident_id, duplicate, suspicious, duplicate_of=None):
        return self.execute(
            "UPDATE incidents SET duplicate_flag = ?, suspicious_flag = ?, duplicate_of = ?, updated_at = ? WHERE id = ?",
            (1 if duplicate else 0, 1 if suspicious else 0, duplicate_of, utc_now(), incident_id),
        )

    def cancel_incident(self, incident_id, reason):
        return self.execute(
            "UPDATE incidents SET status = 'Cancelled', cancellation_reason = ?, updated_at = ? WHERE id = ?",
            (reason, utc_now(), incident_id),
        )

    def close_incident(self, incident_id, notes=""):
        return self.execute(
            "UPDATE incidents SET status = 'Closed', updated_at = ? WHERE id = ?",
            (utc_now(), incident_id),
        )

    def reopen_incident(self, incident_id, new_status="Under Review", reason=""):
        return self.execute(
            "UPDATE incidents SET status = ?, officer_notes = ?, cancellation_reason = NULL, updated_at = ? WHERE id = ?",
            (new_status, f"Reopened: {reason}", utc_now(), incident_id),
        )

    def increment_version(self, incident_id, current_version):
        return self.execute(
            "UPDATE incidents SET version = version + 1, updated_at = ? WHERE id = ? AND version = ?",
            (utc_now(), incident_id, current_version),
        )


class TeamRepository(BaseRepository):
    def list(self):
        return self.fetchall("SELECT * FROM rescue_teams ORDER BY name")

    def get(self, team_id):
        return self.fetchone("SELECT * FROM rescue_teams WHERE id = ?", (team_id,))

    def set_availability(self, team_id, availability):
        return self.execute("UPDATE rescue_teams SET availability = ? WHERE id = ?", (availability, team_id))

    def increment_version(self, team_id, current_version):
        return self.execute(
            "UPDATE rescue_teams SET version = version + 1 WHERE id = ? AND version = ?",
            (team_id, current_version),
        )


class AssignmentRepository(BaseRepository):
    def create_with_id(self, assignment_id, incident_id, team_id, status, officer_id, reason, is_demo=False):
        now = utc_now()
        return self.execute(
            """INSERT INTO assignments
            (id, incident_id, team_id, status, officer_id, recommendation_reason, version, is_demo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (assignment_id, incident_id, team_id, status, officer_id, reason, 1 if is_demo else 0, now),
        )

    def get(self, assignment_id):
        return self.fetchone("SELECT * FROM assignments WHERE id = ?", (assignment_id,))

    def get_latest_for_incident(self, incident_id):
        return self.fetchone("SELECT * FROM assignments WHERE incident_id = ? ORDER BY created_at DESC LIMIT 1", (incident_id,))

    def get_active_for_incident(self, incident_id):
        return self.fetchone(
            """SELECT * FROM assignments
            WHERE incident_id = ? AND status IN ('PendingApproval', 'PendingAcknowledgement', 'Acknowledged', 'Dispatched', 'On Scene')
            ORDER BY created_at DESC LIMIT 1""",
            (incident_id,),
        )

    def get_active_for_team(self, team_id):
        return self.fetchone(
            """SELECT * FROM assignments
            WHERE team_id = ? AND status IN ('PendingApproval', 'PendingAcknowledgement', 'Acknowledged', 'Dispatched', 'On Scene')
            ORDER BY created_at DESC LIMIT 1""",
            (team_id,),
        )

    def list_by_team(self, team_id):
        return self.fetchall(
            """SELECT assignments.*, incidents.description, incidents.address, incidents.severity,
                      incidents.effective_severity, incidents.incident_type, incidents.status AS incident_status
               FROM assignments
               JOIN incidents ON incidents.id = assignments.incident_id
               WHERE assignments.team_id = ? ORDER BY assignments.created_at DESC""",
            (team_id,),
        )

    def update_status(self, assignment_id, status, timestamp_field=None):
        now = utc_now()
        if timestamp_field:
            return self.execute(
                f"UPDATE assignments SET status = ?, {timestamp_field} = ? WHERE id = ?",
                (status, now, assignment_id),
            )
        return self.execute("UPDATE assignments SET status = ? WHERE id = ?", (status, assignment_id))

    def cancel_assignment(self, assignment_id):
        return self.execute("UPDATE assignments SET status = 'Cancelled' WHERE id = ?", (assignment_id,))

    def increment_version(self, assignment_id, current_version):
        return self.execute(
            "UPDATE assignments SET version = version + 1 WHERE id = ? AND version = ?",
            (assignment_id, current_version),
        )


class AuditRepository(BaseRepository):
    def log(self, actor_id, action, entity_type, entity_id, details="", is_demo=False):
        return self.execute(
            """INSERT INTO audit_logs
            (id, actor_id, action, entity_type, entity_id, details, created_at, is_demo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id("audit"), actor_id, action, entity_type, entity_id, details, utc_now(), 1 if is_demo else 0),
        )

    def list(self, action=None, entity_type=None, limit=300):
        sql = """SELECT audit_logs.*, users.display_name AS actor_name, users.username AS actor_username
                 FROM audit_logs
                 LEFT JOIN users ON users.id = audit_logs.actor_id
                 WHERE 1=1"""
        params = []
        if action:
            sql += " AND audit_logs.action = ?"
            params.append(action)
        if entity_type:
            sql += " AND audit_logs.entity_type = ?"
            params.append(entity_type)
        sql += f" ORDER BY audit_logs.created_at DESC LIMIT {int(limit)}"
        return self.fetchall(sql, tuple(params))


class CommunicationRepository(BaseRepository):
    def create(self, incident_id, assignment_id, team_id, channel, status, payload, escalated=False):
        now = utc_now()
        delivered_at = now if status == "Delivered" else None
        return self.execute(
            """INSERT INTO communication_records
            (id, incident_id, assignment_id, recipient_team_id, channel, status, payload, delivered_at, escalated, is_demo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (new_id("comm"), incident_id, assignment_id, team_id, channel, status, payload, delivered_at, 1 if escalated else 0, now),
        )

    def list_by_team(self, team_id):
        return self.fetchall(
            "SELECT * FROM communication_records WHERE recipient_team_id = ? OR recipient_team_id IS NULL ORDER BY created_at DESC LIMIT 100",
            (team_id,),
        )

    def escalation_exists(self, assignment_id):
        row = self.fetchone(
            "SELECT id FROM communication_records WHERE assignment_id = ? AND escalated = 1",
            (assignment_id,),
        )
        return row is not None


class NotificationRepository(BaseRepository):
    def create(self, title, message, user_id=None, team_id=None, role=None, incident_id=None):
        now = utc_now()
        return self.execute(
            """INSERT INTO notifications
            (id, title, message, user_id, team_id, role, incident_id, is_read, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (new_id("notif"), title, message, user_id, team_id, role, incident_id, now),
        )

    def list_for(self, user_id=None, team_id=None, role=None, limit=50):
        sql = "SELECT * FROM notifications WHERE (1=0"
        params = []
        if user_id:
            sql += " OR user_id = ?"
            params.append(user_id)
        if team_id:
            sql += " OR team_id = ?"
            params.append(team_id)
        if role:
            sql += " OR role = ?"
            params.append(role)
        sql += ") ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return self.fetchall(sql, tuple(params))

    def count_unread(self, user_id=None, team_id=None, role=None):
        sql = "SELECT COUNT(*) AS total FROM notifications WHERE is_read = 0 AND (1=0"
        params = []
        if user_id:
            sql += " OR user_id = ?"
            params.append(user_id)
        if team_id:
            sql += " OR team_id = ?"
            params.append(team_id)
        if role:
            sql += " OR role = ?"
            params.append(role)
        sql += ")"
        row = self.fetchone(sql, tuple(params))
        return row["total"] if row else 0

    def mark_as_read(self, notification_id):
        return self.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))

    def mark_all_read(self, user_id=None, team_id=None, role=None):
        sql = "UPDATE notifications SET is_read = 1 WHERE (1=0"
        params = []
        if user_id:
            sql += " OR user_id = ?"
            params.append(user_id)
        if team_id:
            sql += " OR team_id = ?"
            params.append(team_id)
        if role:
            sql += " OR role = ?"
            params.append(role)
        sql += ")"
        return self.execute(sql, tuple(params))


class PasswordResetTokenRepository(BaseRepository):
    def create_token(self, user_id, token_hash, expires_at):
        return self.execute(
            """INSERT INTO password_reset_tokens
            (id, user_id, token_hash, expires_at, used, created_at)
            VALUES (?, ?, ?, ?, 0, ?)""",
            (new_id("tok"), user_id, token_hash, expires_at, utc_now()),
        )

    def find_valid_token(self, token_hash):
        now = utc_now()
        return self.fetchone(
            "SELECT * FROM password_reset_tokens WHERE token_hash = ? AND used = 0 AND expires_at > ?",
            (token_hash, now),
        )

    def mark_used(self, token_id):
        return self.execute("UPDATE password_reset_tokens SET used = 1 WHERE id = ?", (token_id,))


class SchedulerLockRepository(BaseRepository):
    def acquire_lock(self, lock_name, locked_by, duration_seconds=30):
        now = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)).isoformat()
        row = self.fetchone("SELECT * FROM scheduler_locks WHERE lock_name = ?", (lock_name,))
        if not row:
            try:
                self.execute(
                    "INSERT INTO scheduler_locks (lock_name, locked_at, expires_at, locked_by) VALUES (?, ?, ?, ?)",
                    (lock_name, now, expires, locked_by),
                )
                return True
            except Exception:
                return False
        # If lock exists but has expired, claim it
        if row["expires_at"] <= now:
            res = self.execute(
                "UPDATE scheduler_locks SET locked_at = ?, expires_at = ?, locked_by = ? WHERE lock_name = ? AND expires_at = ?",
                (now, expires, locked_by, lock_name, row["expires_at"]),
            )
            return res > 0
        return False

    def release_lock(self, lock_name, locked_by):
        return self.execute("DELETE FROM scheduler_locks WHERE lock_name = ? AND locked_by = ?", (lock_name, locked_by))
