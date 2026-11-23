from dataclasses import dataclass
from typing import Optional


@dataclass
class Role:
    id: str
    name: str
    description: str = ""


@dataclass
class User:
    id: str
    username: str
    display_name: str
    role_id: str
    password_hash: str = ""
    email: str = ""
    contact_number: str = ""
    is_active: bool = True
    failed_login_count: int = 0
    locked_until: Optional[str] = None
    must_change_password: bool = False
    team_id: Optional[str] = None
    created_at: str = ""
    is_demo: bool = False


@dataclass
class Incident:
    id: str
    incident_type: str
    description: str
    address: str
    latitude: float
    longitude: float
    severity: str
    status: str
    reporter_id: str
    reported_severity: str = ""
    effective_severity: str = ""
    priority_score: float = 0
    priority_level: str = ""
    duplicate_flag: bool = False
    suspicious_flag: bool = False
    duplicate_of: Optional[str] = None
    cancellation_reason: Optional[str] = None
    officer_notes: Optional[str] = None
    version: int = 1
    is_demo: bool = False
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.reported_severity:
            self.reported_severity = self.severity
        if not self.effective_severity:
            self.effective_severity = self.severity


@dataclass
class RescueTeam:
    id: str
    name: str
    capabilities: str
    availability: str
    latitude: float = 0
    longitude: float = 0
    version: int = 1
    is_demo: bool = False


@dataclass
class Assignment:
    id: str
    incident_id: str
    team_id: str
    status: str
    officer_id: Optional[str] = None
    acknowledged_at: Optional[str] = None
    dispatched_at: Optional[str] = None
    on_scene_at: Optional[str] = None
    completed_at: Optional[str] = None
    recommendation_reason: Optional[str] = None
    version: int = 1
    is_demo: bool = False
    created_at: str = ""


@dataclass
class Notification:
    id: str
    title: str
    message: str
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    role: Optional[str] = None
    incident_id: Optional[str] = None
    is_read: bool = False
    created_at: str = ""
