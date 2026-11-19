from app.repositories.base import BaseRepository, new_id, utc_now


class UserRepository(BaseRepository):
    def get_by_username(self, username):
        return self.fetchone("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,))

    def get(self, user_id):
        return self.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))

    def list_users(self):
        return self.fetchall("SELECT users.*, roles.name AS role_name FROM users JOIN roles ON roles.id = users.role_id ORDER BY users.display_name")

    def create(self, user_id, username, password_hash, display_name, role_id, is_demo=True):
        return self.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (user_id, username, password_hash, display_name, role_id, 1 if is_demo else 0),
        )

    def set_role(self, user_id, role_id):
        return self.execute("UPDATE users SET role_id = ? WHERE id = ?", (role_id, user_id))


class IncidentRepository(BaseRepository):
    def create(self, incident):
        now = utc_now()
        return self.execute(
            """INSERT INTO incidents
            (id, incident_type, description, address, latitude, longitude, severity, status,
             reporter_id, priority_score, priority_level, duplicate_flag, suspicious_flag, is_demo, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (incident.id, incident.incident_type, incident.description, incident.address, incident.latitude,
             incident.longitude, incident.severity, incident.status, incident.reporter_id, incident.priority_score,
             incident.priority_level, 1 if incident.duplicate_flag else 0, 1 if incident.suspicious_flag else 0,
             now, now),
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

    def update_status(self, incident_id, status):
        return self.execute("UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now(), incident_id))

    def update_flags(self, incident_id, duplicate, suspicious):
        return self.execute(
            "UPDATE incidents SET duplicate_flag = ?, suspicious_flag = ?, updated_at = ? WHERE id = ?",
            (1 if duplicate else 0, 1 if suspicious else 0, utc_now(), incident_id),
        )

    def update_priority(self, incident_id, score, level):
        return self.execute(
            "UPDATE incidents SET priority_score = ?, priority_level = ?, updated_at = ? WHERE id = ?",
            (score, level, utc_now(), incident_id),
        )


class TeamRepository(BaseRepository):
    def list(self):
        return self.fetchall("SELECT * FROM rescue_teams ORDER BY id")

    def get(self, team_id):
        return self.fetchone("SELECT * FROM rescue_teams WHERE id = ?", (team_id,))

    def list_resources(self, team_id=None):
        sql = "SELECT resources.*, rescue_teams.name AS team_name FROM resources LEFT JOIN rescue_teams ON rescue_teams.id = resources.team_id"
        params = ()
        if team_id:
            sql += " WHERE resources.team_id = ?"
            params = (team_id,)
        return self.fetchall(sql, params)

    def set_availability(self, team_id, availability):
        return self.execute("UPDATE rescue_teams SET availability = ? WHERE id = ?", (availability, team_id))


class AssignmentRepository(BaseRepository):
    def create(self, incident_id, team_id, status, officer_id, reason):
        return self.execute(
            """INSERT INTO assignments
            (id, incident_id, team_id, status, officer_id, recommendation_reason, is_demo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (new_id("assignment"), incident_id, team_id, status, officer_id, reason, utc_now()),
        )

    def create_with_id(self, assignment_id, incident_id, team_id, status, officer_id, reason):
        return self.execute(
            """INSERT INTO assignments
            (id, incident_id, team_id, status, officer_id, recommendation_reason, is_demo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (assignment_id, incident_id, team_id, status, officer_id, reason, utc_now()),
        )

    def get_by_incident(self, incident_id):
        return self.fetchone("SELECT * FROM assignments WHERE incident_id = ? ORDER BY created_at DESC", (incident_id,))

    def list_for_team(self, team_id):
        return self.fetchall(
            """SELECT assignments.*, incidents.incident_type, incidents.description, incidents.address,
               incidents.severity, incidents.status AS incident_status
               FROM assignments JOIN incidents ON incidents.id = assignments.incident_id
               WHERE assignments.team_id = ? ORDER BY assignments.created_at DESC""",
            (team_id,),
        )

    def update_status(self, assignment_id, status, timestamp_field=None):
        allowed_fields = {"acknowledged_at", "dispatched_at", "on_scene_at", "completed_at"}
        if timestamp_field in allowed_fields:
            sql = f"UPDATE assignments SET status = ?, {timestamp_field} = ? WHERE id = ?"
            return self.execute(sql, (status, utc_now(), assignment_id))
        return self.execute("UPDATE assignments SET status = ? WHERE id = ?", (status, assignment_id))

    def pending_acknowledgements(self, timeout_seconds):
        return self.fetchall(
            "SELECT * FROM assignments WHERE status IN ('PendingAcknowledgement', 'PendingApproval')",
            (),
        )


class AuditRepository(BaseRepository):
    def log(self, actor_id, action, entity_type, entity_id, details):
        return self.execute(
            "INSERT INTO audit_logs (id, actor_id, action, entity_type, entity_id, details, created_at, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (new_id("audit"), actor_id, action, entity_type, entity_id, details, utc_now()),
        )

    def list(self, action=None, entity_type=None):
        sql = "SELECT audit_logs.*, users.display_name AS actor_name FROM audit_logs LEFT JOIN users ON users.id = audit_logs.actor_id WHERE 1=1"
        params = []
        if action:
            sql += " AND audit_logs.action = ?"
            params.append(action)
        if entity_type:
            sql += " AND audit_logs.entity_type = ?"
            params.append(entity_type)
        sql += " ORDER BY audit_logs.created_at DESC LIMIT 300"
        return self.fetchall(sql, tuple(params))


class CommunicationRepository(BaseRepository):
    def create(self, incident_id, assignment_id, team_id, channel, status, payload, escalated=False):
        return self.execute(
            """INSERT INTO communication_records
            (id, incident_id, assignment_id, recipient_team_id, channel, status, payload, delivered_at, escalated, is_demo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (new_id("comm"), incident_id, assignment_id, team_id, channel, status, payload,
             utc_now() if status == "Delivered" else None, 1 if escalated else 0, utc_now()),
        )

    def escalation_exists(self, assignment_id):
        row = self.fetchone("SELECT id FROM communication_records WHERE assignment_id = ? AND escalated = 1", (assignment_id,))
        return row is not None

    def list_for_recipient(self, team_id):
        return self.fetchall(
            "SELECT * FROM communication_records WHERE recipient_team_id = ? OR recipient_team_id IS NULL ORDER BY created_at DESC LIMIT 100",
            (team_id,),
        )
