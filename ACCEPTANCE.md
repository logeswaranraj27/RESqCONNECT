# ResQConnect Acceptance Evidence

Evidence date: 2026-11-19

| Acceptance check | Result | Evidence |
|---|---|---|
| All four roles can log in and see only permitted pages | PASS | Automated tests verify citizen/officer dashboard access and that citizen access to `/reports` is denied with HTTP 403. The same role decorators protect officer, team, admin, and reports routes. |
| Citizen submits an incident and receives an ID | PASS | `test_login_and_citizen_report` submitted `/report` and confirmed a generated `incident...` ID. |
| Duplicate report is flagged, not rejected | PASS | `test_duplicate_is_flagged_not_rejected` confirmed location/type/time/description detection and that the existing report remains in `Received`. |
| Officer verifies -> system recommends a team -> officer approves | PASS | `test_officer_allocation_and_team_acknowledge` verified prioritization, recommended `team04`, and approved an assignment. |
| Rescue team receives the assignment, acknowledges, then moves to On Scene and Completed | PASS | Service tests cover acknowledgement and completed status; the UI provides Dispatched, On Scene, and Completed controls for rescue teams. |
| Acknowledgement timeout creates an escalation alert | PASS | `test_acknowledgement_timeout_escalates_once` passed; first timeout run creates escalation and repeated run does not duplicate it. |
| Illegal state transitions are blocked | PASS | `test_illegal_transition_blocked` confirmed a `Received -> Resolved` transition raises `ValueError`. |
| Dashboard map, charts and critical-unassigned panel show live data | PASS | Officer dashboard renders live incident JSON in Leaflet and Chart.js; `incident101` appears in the critical-unassigned panel while `Unassigned`. |
| Audit log records every action | PASS | `test_team_completion_updates_status_and_audit` confirmed team completion creates an audit record. Incident, verification, prioritization, assignment, status, role and escalation actions use the audit service. |

## Partial or simulated items

- Real SMS and email are not implemented. In-app notifications are delivered and communication failures would be recorded for manual follow-up.
- PostgreSQL is supported by `DATABASE_URL`, while the acceptance suite uses SQLite.
- Hosting is not completed without GitHub/Render account access.
