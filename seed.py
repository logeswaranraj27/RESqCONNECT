"""Seed demo data for local and PostgreSQL databases."""
from werkzeug.security import generate_password_hash
import os

from app import create_app


app = create_app()
database = app.extensions["database"]
demo_password = os.getenv("DEMO_PASSWORD", "demo1234")

roles = [
    ("role-citizen", "Citizen", "Reports and tracks incidents"),
    ("role-officer", "Emergency Officer", "Verifies, prioritizes, and assigns incidents"),
    ("role-team", "Rescue Team", "Acknowledges and executes assignments"),
    ("role-admin", "Administrator", "Manages users, roles, and settings"),
]

users = [
    ("citizen1", "citizen1", "Demo Citizen", "role-citizen"),
    ("officer1", "officer1", "Demo Emergency Officer", "role-officer"),
    ("rescue1", "rescue1", "Demo Rescue Team Coordinator", "role-team"),
    ("admin1", "admin1", "Demo Administrator", "role-admin"),
]

teams = [
    ("team01", "Metro Fire Response", "FireRescue,Medical", "Available", 13.0827, 80.2707),
    ("team02", "Coastal Flood Unit", "FloodRescue,Boat", "Available", 13.0674, 80.2815),
    ("team03", "Urban Heavy Rescue", "HeavyRescue,General", "Limited", 13.0522, 80.2543),
    ("team04", "FloodRescue Team 04", "FloodRescue,Boat,Medical", "Available", 13.0878, 80.2785),
    ("team05", "Medical Emergency Unit", "Medical,General", "Available", 13.0698, 80.2439),
    ("team06", "General Disaster Response", "General,Earthquake,Medical", "Available", 13.0108, 80.2157),
]

incidents = [
    ("incident101", "Flood", "Severe flash flood with stranded families near a low-lying residential block.", "Adyar low-lying zone, Chennai", 13.0067, 80.2206, "Critical", "Unassigned", "citizen1"),
    ("incident102", "Fire", "Apartment fire reported on the second floor.", "Anna Nagar, Chennai", 13.0850, 80.2101, "High", "Received", "citizen1"),
    ("incident103", "Fire", "Second-floor apartment fire with smoke in Anna Nagar.", "Anna Nagar Central, Chennai", 13.0854, 80.2105, "High", "Duplicate Review", "citizen1"),
    ("incident104", "Flood", "Road flooding blocking emergency access.", "Guindy, Chennai", 13.0102, 80.2125, "Medium", "Verified", "citizen1"),
    ("incident105", "Earthquake", "Partial wall collapse and residents trapped near the market.", "T. Nagar, Chennai", 13.0418, 80.2341, "Critical", "Verified", "citizen1"),
    ("incident106", "Other", "Large fallen tree and damaged power lines on a residential street.", "Velachery, Chennai", 12.9815, 80.2180, "Medium", "Under Review", "citizen1"),
    ("incident107", "Fire", "Test prank report about smoke in an empty building.", "Saidapet, Chennai", 13.0210, 80.2230, "Low", "Verification Required", "citizen1"),
    ("incident108", "Flood", "Families isolated by rising water near the bus depot.", "Mylapore, Chennai", 13.0339, 80.2619, "High", "Prioritized", "citizen1"),
    ("incident109", "Fire", "Warehouse fire with visible flames and staff awaiting evacuation.", "Ambattur, Chennai", 13.1143, 80.1548, "Critical", "Assigned", "citizen1"),
    ("incident110", "Earthquake", "Aftershock damage and minor structural cracks reported.", "Porur, Chennai", 13.0355, 80.1585, "Medium", "In Progress", "citizen1"),
    ("incident111", "Flood", "Stormwater breach affecting a ground-floor clinic.", "Chromepet, Chennai", 12.9516, 80.1407, "High", "Resolved", "citizen1"),
    ("incident112", "Other", "Power outage affecting a public shelter.", "Sholinganallur, Chennai", 12.9010, 80.2279, "Low", "Closed", "citizen1"),
]

connection = database.connect()
try:
    cursor = connection.cursor()
    if database.is_postgres:
        for role_id, name, description in roles:
            cursor.execute(
                "INSERT INTO roles (id, name, description) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description",
                (role_id, name, description),
            )
    else:
        for role_id, name, description in roles:
            cursor.execute(
                "INSERT INTO roles (id, name, description) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
                (role_id, name, description),
            )

    # Demo seed is intentionally resettable; live data is never part of this script.
    demo_tables = [
        "communication_records", "audit_logs", "status_updates", "assignments",
        "resources", "verification_records", "incidents", "rescue_teams", "users",
    ]
    for table in demo_tables:
        cursor.execute(database.placeholder_sql(f"DELETE FROM {table} WHERE is_demo = 1"))

    for username, password, display_name, role_id in users:
        cursor.execute(
            database.placeholder_sql(
                "INSERT INTO users (id, username, password_hash, display_name, role_id, is_active, is_demo) "
                "VALUES (?, ?, ?, ?, ?, 1, 1)"
            ),
            (username, username, generate_password_hash(demo_password), display_name, role_id),
        )

    for team_id, name, capabilities, availability, latitude, longitude in teams:
        cursor.execute(
            database.placeholder_sql(
                "INSERT INTO rescue_teams (id, name, capabilities, availability, latitude, longitude, is_demo) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)"
            ),
            (team_id, name, capabilities, availability, latitude, longitude),
        )

    for incident_id, incident_type, description, address, latitude, longitude, severity, status, reporter in incidents:
        duplicate = incident_id == "incident103"
        suspicious = incident_id == "incident107"
        cursor.execute(
            database.placeholder_sql(
                """INSERT INTO incidents
                (id, incident_type, description, address, latitude, longitude, severity, status,
                 reporter_id, priority_score, priority_level, duplicate_flag, suspicious_flag, is_demo, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?, 1,
                        '2026-11-19T10:00:00+00:00', '2026-11-19T10:00:00+00:00')"""
            ),
            (incident_id, incident_type, description, address, latitude, longitude, severity, status,
             reporter, 1 if duplicate else 0, 1 if suspicious else 0),
        )

    resources = [
        ("res-team04-floodboat", "team04", "Flood rescue boat", "Boat", 2),
        ("res-team04-medkit", "team04", "Emergency medical kit", "Medical", 8),
        ("res-team04-radio", "team04", "Field radio", "Communication", 6),
        ("res-team01-pump", "team01", "Fire pump", "Fire", 3),
        ("res-team03-lift", "team03", "Hydraulic lift", "HeavyEquipment", 2),
        ("res-team06-kit", "team06", "General response kit", "General", 10),
    ]
    for resource_id, team_id, name, resource_type, quantity in resources:
        cursor.execute(
            database.placeholder_sql(
                "INSERT INTO resources (id, team_id, name, resource_type, quantity, is_demo) VALUES (?, ?, ?, ?, ?, 1)"
            ),
            (resource_id, team_id, name, resource_type, quantity),
        )

    cursor.execute(
        database.placeholder_sql(
            """INSERT INTO assignments
            (id, incident_id, team_id, status, officer_id, recommendation_reason, is_demo, created_at)
            VALUES ('assignment01', 'incident101', 'team04', 'PendingApproval', 'officer1',
                    'Demo recommendation: FloodRescue capability and Available status', 1,
                    '2026-11-19T10:00:00+00:00')"""
        )
    )

    cursor.execute(
        database.placeholder_sql(
            """INSERT INTO assignments
            (id, incident_id, team_id, status, officer_id, acknowledged_at, dispatched_at, recommendation_reason, is_demo, created_at)
            VALUES ('assignment02', 'incident109', 'team01', 'Acknowledged', 'officer1',
                    '2026-11-19T10:10:00+00:00', '2026-11-19T10:12:00+00:00',
                    'Demo FireRescue capability match', 1, '2026-11-19T10:05:00+00:00')"""
        )
    )

    cursor.execute(
        database.placeholder_sql(
            """INSERT INTO communication_records
            (id, incident_id, assignment_id, recipient_team_id, channel, status, payload, delivered_at, escalated, is_demo, created_at)
            VALUES ('comm-demo-01', 'incident101', 'assignment01', 'team04', 'InApp',
                    'Delivered', 'Demo in-app assignment notification', '2026-11-19T10:00:01+00:00', 0, 1,
                    '2026-11-19T10:00:00+00:00')"""
        )
    )
    connection.commit()
finally:
    connection.close()

print("ResQConnect demo data seeded.")
