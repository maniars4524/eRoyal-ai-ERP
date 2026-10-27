# eSway ERP 2.0 - run it and deploy it

## 1. Run it on one computer (manual, no scripts needed)

Needs: Windows / Linux / macOS, Docker Desktop (for PostgreSQL), Python 3.12.

1. Copy `.env.example` to `.env` and fill it in (database URL, `JWT_SECRET`, Super Admin e-mail / username / password).
2. Start the database: `docker compose up -d postgres`
3. In `backend/`: `python -m venv .venv`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt` (Linux/macOS: `.venv/bin/python`).
4. Update the database and create the first records: `python -m alembic upgrade head` then `python -m app.seed`
5. Start the app: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` (`0.0.0.0` = listen on every network card; use `127.0.0.1` to keep it on this computer only)
6. Open **http://127.0.0.1:8000/erp/login**. Ctrl+C stops it.
7. For other computers on the same network: run `ALLOW-NETWORK.cmd` once as administrator (opens port 8000 in the Windows firewall for the local network), then open `http://<this PC's address>:8000/erp/login` or `http://<PC name>:8000/erp/login`. This PC's address can change (Wi-Fi/DHCP) - `ipconfig` shows it; the PC name does not change.

One click on Windows: double-click `RUN-LOCAL.cmd` (does steps 2 to 5). It now listens on all network cards and prints this PC's addresses. `RUN-LOCAL.cmd 8002` runs it on another port.

Portals: `/erp/login` (company staff), `/demo/login` (demo data), `/developer/login` (platform administration). Which of them are reachable from outside is set by `PUBLIC_PORTALS` in the env file.

## 2. The ports used on this machine

| Port | What | Env file |
|---|---|---|
| 8000 | the live app (all network cards, database esway_erp) | `.env` |
| 8001 | shared / public app (all network cards) | `.env.demo` |
| 8002 | same as 8001, for the VS Code dev tunnel | `.env.demo` |
| 8010 | QA / automated UI tests | `.env.qa` |
| 5432 | PostgreSQL in Docker | - |

## 3. Port forwarding / dev tunnel shows "HTTP ERROR 502"

502 means the tunnel is fine but **nothing is listening on the forwarded port**. Check in this order:

1. Which port did you forward (e.g. 8002)? Open `http://127.0.0.1:<port>/api/health` on this computer. If it does not answer, start the app on that port (`RUN-LOCAL.cmd 8002`).
2. The app must listen on `0.0.0.0` (not only `127.0.0.1`) when you forward it; `RUN-LOCAL.cmd` does this for any port other than 8000.
3. Docker Desktop must be running (the database lives there). After a restart of the computer run `RUN-LOCAL.cmd` or `START-PUBLIC.cmd` again.
4. **Other computers are asked to sign in / get "access denied"**: a VS Code dev tunnel is *Private* by default, so only you (signed in with the same GitHub / Microsoft account) can open it; everyone else is redirected to a GitHub sign-in and refused. The app is not the problem. In VS Code open the **Ports** tab, right-click the port (8002) > **Port Visibility** > **Public**. (Command line: `devtunnel port update <tunnel-id> -p 8002 --allow-anonymous`.) Only do this for the shared/demo instance, never for the private one on 8000.
5. Quick public link without an account: `tools\cloudflared.exe tunnel --url http://127.0.0.1:8001` (the address changes each time).

## 4. Deploy it on other systems / globally

The whole product is one container (FastAPI serves the API and the screens) plus PostgreSQL.

**A. Any server with Docker (VPS, office server, cloud VM)**
1. Copy the project folder to the server. Create `.env` with `APP_ENV=production`, a random `JWT_SECRET` (32+ characters), a strong Super Admin password and the `DATABASE_URL`.
2. `docker compose up -d` (database) and build the app image: `docker build -t esway-erp .` then `docker run -d -p 8000:8000 --env-file .env esway-erp`. The image runs the migrations and the seed on every start.
3. Put a reverse proxy with HTTPS in front (Caddy, Nginx or Cloudflare). Open only ports 80 and 443 to the internet; never expose 5432.

**B. Render** (config already in `render.yaml`): connect the repository, set `SUPER_ADMIN_EMAIL`, `SUPER_ADMIN_USERNAME`, `SUPER_ADMIN_PASSWORD` in the dashboard. It creates the web service and the database. The free plan sleeps; use a paid plan for real work.

**C. Another customer / another office**: one installation can hold many companies (multi-tenant). Create each company in the developer portal; each gets its own license number, users and data. Install a separate copy only when the customer wants their own server.

## 5. Before real data goes in
- Strong, different passwords for every user (the demo / test passwords are weak on purpose).
- `APP_ENV=production` and a real `JWT_SECRET`.
- Nightly database backup (`pg_dump`) copied off the machine, and a restore test.
- HTTPS only. Keep the demo portal off a production server (`PUBLIC_PORTALS=erp`).
- Never commit `.env` files.
