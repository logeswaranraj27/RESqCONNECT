CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role_id TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_demo INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (role_id) REFERENCES roles(id)
);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    incident_type TEXT NOT NULL,
    description TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    reporter_id TEXT NOT NULL,
    priority_score REAL NOT NULL DEFAULT 0,
    priority_level TEXT,
    duplicate_flag INTEGER NOT NULL DEFAULT 0,
    suspicious_flag INTEGER NOT NULL DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (reporter_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS verification_records (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    reviewer_id TEXT,
    method TEXT NOT NULL,
    result TEXT NOT NULL,
    notes TEXT,
    reviewed_at TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (incident_id) REFERENCES incidents(id),
    FOREIGN KEY (reviewer_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS rescue_teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capabilities TEXT NOT NULL,
    availability TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    is_demo INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    team_id TEXT,
    name TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (team_id) REFERENCES rescue_teams(id)
);

CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    status TEXT NOT NULL,
    officer_id TEXT,
    acknowledged_at TEXT,
    dispatched_at TEXT,
    on_scene_at TEXT,
    completed_at TEXT,
    recommendation_reason TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(id),
    FOREIGN KEY (team_id) REFERENCES rescue_teams(id),
    FOREIGN KEY (officer_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS status_updates (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    actor_id TEXT,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (incident_id) REFERENCES incidents(id),
    FOREIGN KEY (actor_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS communication_records (
    id TEXT PRIMARY KEY,
    incident_id TEXT,
    assignment_id TEXT,
    recipient_team_id TEXT,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT,
    delivered_at TEXT,
    escalated INTEGER NOT NULL DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (assignment_id) REFERENCES assignments(id),
    FOREIGN KEY (recipient_team_id) REFERENCES rescue_teams(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (actor_id) REFERENCES users(id)
);
