# eRoyal Freight ERP

Local full-stack conversion of the eRoyal Freight prototype.

The original `eRoyal-ai-ERP.html` remains the complete UI reference and is served by the React/Vite shell at `/prototype`. This preserves every existing screen, navigation flow, chart, wizard, modal, drawer, chat panel, and self-contained asset while the backend APIs and database are introduced incrementally behind it.

## Stack

- React and Vite: local frontend entry point at `http://127.0.0.1:5173`
- FastAPI and Python: API at `http://127.0.0.1:8000`
- PostgreSQL 16: Docker service at `127.0.0.1:5432`
- SQLAlchemy: Python persistence models in `backend/app/models.py`

## Start locally

Prerequisites: Node.js 20+, Python 3.11+, Docker Desktop with the WSL 2 backend enabled.

If Docker Desktop cannot start and Windows reports that WSL is not installed, open **PowerShell as Administrator** and run `wsl --install`, then restart Windows and Docker Desktop. This is a machine prerequisite; the project will not switch away from PostgreSQL.

```powershell
cd e:\eRoyal-ai-ERP
Copy-Item .env.example .env
docker compose up -d postgres

cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Apply the foundation migration and seed a platform administrator after PostgreSQL is healthy:

```powershell
cd e:\eRoyal-ai-ERP\backend
python -m alembic upgrade head
$env:SUPER_ADMIN_PASSWORD = "use-a-local-password-of-12-or-more-characters"
python -m app.seed
```

In a second terminal:

```powershell
cd e:\eRoyal-ai-ERP\frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The existing demo credentials remain `mani` / `mani`.

## Local login accounts

Use the single browser URL for both roles:

```text
URL: http://127.0.0.1:5173

Super Admin
Username: Admin
Password: admin

ERP User
Username: mani
Password: mani
Company: NRS FREIGHT
```

These accounts are created by `python -m app.seed` for local development only. Change the seeded passwords before using the system beyond a local demo.

## VS Code tasks

Run `ERP: Start PostgreSQL`, `ERP: Start FastAPI`, and `ERP: Start React` from the Tasks menu. The frontend shows a small status dot for the API health endpoint; the prototype itself remains the full-screen ERP experience.

## Migration boundary

The UI is deliberately preserved as-is first. Phase 01 adds the authentication and tenant foundation behind stable API contracts. New backend modules should replace the prototype's in-memory actions behind the same boundary, starting with organization setup and users. The redaction rule for sensitive government identifiers remains enforced in the UI and data contracts.
