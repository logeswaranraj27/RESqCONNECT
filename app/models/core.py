from dataclasses import dataclass


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
    is_active: bool = True
    is_demo: bool = True


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
    priority_score: float = 0
    priority_level: str = ""
    duplicate_flag: bool = False
    suspicious_flag: bool = False
    is_demo: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RescueTeam:
    id: str
    name: str
    capabilities: str
    availability: str
    latitude: float = 0
    longitude: float = 0
    is_demo: bool = True
