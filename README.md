# ResQConnect

ResQConnect is a demo real-time disaster response and emergency resource coordination system. It supports the workflow:

**Report -> Verify -> Prioritize -> Allocate -> Dispatch -> Monitor -> Resolve**

**Important:** ResQConnect does not replace official emergency helplines such as 112. It is a coordination workflow for incidents already known to the organization.

## Demo credentials

Every account uses password `demo1234`:

| Role | Username |
|---|---|
| Citizen | `citizen1` |
| Emergency Officer | `officer1` |
| Rescue Team | `rescue1` |
| Administrator | `admin1` |

All seeded people, teams, incidents, assignments, and audit records are clearly demo data.

## One-command local setup

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "from app import create_app; create_app()"
python seed.py
python -m flask --app app:create_app run --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000/`.

## Configuration

Copy `.env.example` to `.env`. `SECRET_KEY` must be changed for deployment.

- `DATABASE_URL`: SQLite development default or PostgreSQL URL such as `postgresql://user:password@host/database`.
- `ACKNOWLEDGEMENT_TIMEOUT_SECONDS`: acknowledgement timeout. Default `120`.
- `DEMO_ACKNOWLEDGEMENT_TIMEOUT_SECONDS`: optional short demo override. When set, it takes precedence over `ACKNOWLEDGEMENT_TIMEOUT_SECONDS` for acknowledgement testing.
- `DEMO_PASSWORD`: password for every seeded demo account. Defaults to `demo1234` locally. Use a strong unique value in hosted environments.
- `PORT`: application port.

## Database

The same repository and schema are used for SQLite and PostgreSQL. SQL is parameterized and PostgreSQL-compatible.

```powershell
python -c "from app import create_app; create_app()"
python seed.py
```

## Seeding a hosted PostgreSQL database

From a local machine, use the **External Database URL** supplied by the Render PostgreSQL dashboard. Never commit this URL.

PowerShell:

```powershell
$env:DATABASE_URL="postgresql://DATABASE_USER:DATABASE_PASSWORD@EXTERNAL_HOST/DATABASE_NAME"
$env:DEMO_PASSWORD="use-a-strong-generated-hosted-password"
python -c "from app import create_app; create_app()"
python seed.py
```

This initializes the tables through `app:create_app()`, then resets and recreates only records marked `is_demo = 1`. Real non-demo records are not deleted by the demo seed.

## Docker

```powershell
docker build -t resqconnect .
docker run --rm -p 10000:10000 --env-file .env resqconnect
```

## Render deployment

1. Create a GitHub repository and push this project.
2. In Render, choose **New > Web Service**.
3. Connect the GitHub repository and choose Docker deployment.
4. Set `SECRET_KEY` to a secure generated value.
5. Set `ACKNOWLEDGEMENT_TIMEOUT_SECONDS=120`.
6. Set `FLASK_ENV=production`.
7. Create a Render PostgreSQL database. Copy its internal or external database URL into `DATABASE_URL`.
8. Deploy. Gunicorn runs with one worker because acknowledgement escalation is database-backed and deduplicated.

SQLite is supported for local development only on Render: the free-tier filesystem is ephemeral and demo data or changes may disappear after redeploy or restart. Use PostgreSQL for persistent hosting.

GitHub or Render account login is required only at the hosting step. No account login can be performed by this application.

## Simulated features

- SMS and email gateways are **simulated** only. ResQConnect records in-app simulated delivery and does not claim to send real SMS or email.
- Seed records are demo data.
- Live notifications are provided through dashboard polling/API refresh rather than a production push provider.
- PostgreSQL support is implemented but the local acceptance run uses SQLite.

## Tests

```powershell
python -m pytest -q
```

Real local verification, run with the project virtual environment:

```text
....... [100%]
7 passed in 6.04s
```

## Acceptance results

| Acceptance check | Result | Evidence |
|---|---|---|
| All four roles can log in and see permitted pages | PASS | Focused tests verify role dashboard access and citizen denial of the reports page with HTTP 403. |
| Citizen submits an incident and receives an ID | PASS | Automated report test confirms a generated `incident...` ID. |
| Duplicate report is flagged, not rejected | PASS | Automated duplicate test confirms proximity/type/time/description detection and existing-report preservation. |
| Officer verifies, recommends, and approves | PASS | Automated test verifies priority calculation and `team04` assignment approval. |
| Rescue team acknowledges and completes | PASS | Automated team service test confirms acknowledgement and completion transitions. |
| Acknowledgement timeout escalates once | PASS | Automated timeout test confirms escalation is created once and then deduplicated. |
| Illegal transitions are blocked | PASS | Automated test rejects `Received -> Resolved`. |
| Dashboard map, charts, and critical panel show data | PASS | Officer dashboard renders live incident JSON and charts; `incident101` is demo critical/unassigned data. |
| Audit log records actions | PASS | Automated audit test confirms completed team status is recorded. |
