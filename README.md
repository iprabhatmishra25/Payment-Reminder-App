# Duewise — Payment Reminder Desktop App

A working version-one desktop application for company invoices, verified payments, monthly reminders, and company-count payment reports.

**Architecture:** Electron / React / TypeScript → FastAPI → PostgreSQL. Celery Beat → Redis → Celery worker → Resend email delivery. SQLite is supported only as a convenient local development/demo database.

## What is implemented

- Login, Argon2id password hashing, TOTP MFA enrollment/sign-in, recovery email/token reset, revocable sessions, refresh-token rotation, server-side role permissions, and shared database-backed rate limits.
- Company search, create/edit/archive/restore, one billing contact per company, and reminder pause/resume.
- Posted/draft invoices, validation, posting, and administrator-only voiding with reasons.
- Verified full/partial payment entries, exact decimal calculations, request idempotency, concurrent overpayment prevention, and audited reversals.
- Reminder previews, grouped company emails, monthly calendar schedules, company/month deduplication, transactional outbox, bounded retries, delivery history, signed provider webhooks, and administrator review of uncertain sends.
- Monthly pie chart, distinct-company outcomes, partial-payment handling, company drill-down, six-month cash-receipt trend, server-side pagination/search, and CSV export with formula-injection protection.
- Admin user creation/disable, schedule/template settings, audit history, operational status endpoint, and server connection settings on the desktop login screen.
- Electron packaging configuration, backend migrations, Docker Compose, HTTPS proxy configuration, backend tests, and a real Electron end-to-end test.

The first version has one operating business, INR currency, one billing contact per company, and one invoice per payment entry. Banking/payment-gateway integration, multi-invoice receipt allocation, refunds/credits/overpayments, offline writes, and automatic desktop updates are not included.

## 1. Start a local demo on Windows or Ubuntu

Native x64 installers are produced for **Windows (`.exe`)** and **Ubuntu (`.deb` and `.AppImage`)**. See [Windows and Ubuntu setup](docs/WINDOWS_UBUNTU.md) for installer downloads and step-by-step instructions. Desktop clients can share one always-on backend.

For source/demo use, install Python 3.12, Node.js 24 LTS (including npm), and Git. Use a normal graphical desktop session.

Windows PowerShell or Command Prompt:

```powershell
py -3.12 scripts/setup.py --demo
py -3.12 scripts/run_local.py --demo
```

Ubuntu:

```bash
python3 scripts/setup.py --demo
python3 scripts/run_local.py --demo
```

No virtual-environment activation or PowerShell policy change is needed. The root `Start-Demo-Windows.cmd` and `start-demo-ubuntu.sh` launchers are also provided. Setup creates the virtual environment and installs dependencies; the launcher applies migrations, seeds labeled sample data, and starts the API, preview-only worker, Vite, and Electron. The local demo database is `services/backend/duewise.db`.

Demo credentials:

```text
Email:    demo@duewise.example.com
Password: DemoOnly!2026
```

The credentials are deliberately public demo credentials. Demo data and sends are labeled. No email is sent in preview mode. The demo database is blocked from production startup. Use a clean database and real administrator account for production.

If Electron is unavailable, inspect the React interface during development:

```bash
python scripts/run_local.py --demo --browser-only
```

Open `http://127.0.0.1:5173`. This is a development preview; the installed Electron application keeps credentials in its main-process memory. Browser preview credentials also stay in memory, never local storage.

## 2. Start an empty local workspace

Instead of demo setup:

```bash
python scripts/configure.py
```

For manual backend commands, load the root configuration in the current terminal using Python dotenv's runner, or set environment variables in your normal environment. The launcher loads the root `.env` automatically.

For an empty local workspace, run `scripts/setup.py` without `--demo`, then use the portable backend commands. On Windows use `py -3.12` instead of `python3`:

```bash
python3 scripts/backend.py migrate
python3 scripts/backend.py create-admin --email admin@your-company.com --name "Your Name"
python3 scripts/run_local.py
```

The administrator password is entered interactively and is never embedded in code or a shell argument. For actual shared use, use PostgreSQL and the deployment steps below.

## 3. Use PostgreSQL and the background services

The shared server runs on Ubuntu or in Linux containers. Windows hosts can use Docker Desktop with WSL2. Native Windows production Celery workers are not supported; Windows desktop clients use the same HTTPS API.

Install Docker Engine and the Compose plugin on an always-on Linux server.

Create configuration once:

```bash
python3 scripts/configure.py --production
```

Edit `.env`:

- Keep `DEMO_MODE=false`.
- Use the generated `POSTGRES_PASSWORD` and `ENCRYPTION_KEY`.
- Set `PUBLIC_HOST` to your real hostname if using the HTTPS profile.
- Keep `EMAIL_MODE=preview` during initial setup.
- The Compose file overrides `DATABASE_URL` and `REDIS_URL` with the internal server addresses.

Launch the services from the project root:

```bash
docker compose --env-file .env -f infra/compose.yml up -d --build
```

Create the administrator:

```bash
docker compose --env-file .env -f infra/compose.yml exec api python -m app.cli create-admin --email admin@your-company.com --name "Your Name"
```

For HTTPS, point the hostname to the server, allow ports 80 and 443, and start the proxy profile:

```bash
docker compose --env-file .env -f infra/compose.yml --profile https up -d
```

Database and Redis ports are not published. The API is bound to server loopback unless accessed through the HTTPS proxy. In production, administrator and accounts roles must enroll MFA before accessing financial data.

**Do not increase the scheduler replica count.** Multiple worker processes are supported with database claims and monthly deduplication. The default process rate limit assumes one worker instance; coordinate provider rate limits before adding worker instances.

The Docker configuration is supplied for deployment. Check the validation report for which environments were actually exercised during creation; configuration files alone do not establish a successful deployment.

## 4. Configure real reminder delivery

Use a transactional Resend account and a verified sender domain. Configure on the server only:

```text
EMAIL_MODE=resend
EMAIL_API_KEY=<your private Resend API key>
EMAIL_FROM=Accounts <accounts@your-verified-domain.com>
EMAIL_ALLOWED_RECIPIENTS=<optional comma-separated test recipients>
EMAIL_WEBHOOK_SECRET=<your Resend webhook signing secret>
```

Keep an allowlist during the pilot. Never use demo mode for live sending. Configure the provider webhook URL:

```text
https://your-server.example/api/v1/webhooks/email
```

Subscribe to supported sent, delivered, bounced, complained, and delayed events. Signatures use the unmodified raw body, a five-minute timestamp tolerance, constant-time comparison, and event-ID deduplication. Delivery/bounce events arriving before the send response are also retained and applied.

Restart services after changing environment variables:

```bash
docker compose --env-file .env -f infra/compose.yml up -d --force-recreate api worker scheduler
```

In Settings, set monthly day/time/timezone and enable the schedule. Run a manual preview and controlled live test before enabling automatic sending for all companies.

## 5. Reminder behavior

- Only posted invoices with positive outstanding balances and due dates before today in the business timezone qualify.
- A company receives one grouped monthly reminder. Manual and automatic runs share the same company/month key.
- Paused companies and held invoices are excluded. Archiving prevents new invoices but does not erase debt or silently stop reminders; pause separately.
- Workers recheck balances and recipient changes before sending.
- `PREVIEW` never means sent and never contributes to the pie chart.
- `SENT` means provider accepted, not inbox delivery. `DELIVERED` needs a verified provider event.
- `UNKNOWN` is used for ambiguous external outcomes or worker crashes during send. An administrator reviews provider evidence, then confirms sent/not sent. Do not blindly resend uncertain work.
- Temporary provider rate limits receive bounded retries. Permanent failures can be manually reviewed/retried, with at most five attempts on a logical reminder.
- A late payment can race an email already in flight. The default wording acknowledges recently made payments.
- A daily/monthly scheduler scan is not a 30-day timer. Dates 29–31 clamp to the last day in shorter months; missed runs are caught up only within the configured window.

## 6. Reporting definition

The selected **billing month** is the invoice cohort. The status observation time is displayed separately. An October invoice reminded in November stays in the October invoice report.

The main pie counts distinct companies with a qualifying provider-accepted reminder mentioning that month's invoices:

- Paid after reminder: all relevant monthly balances are zero and settlement occurred after the first qualifying reminder.
- Pending after reminder: at least one relevant monthly invoice has a positive balance; partial payment stays pending.

Companies with no successful reminder appear in a separate follow-up card. Backdated receipts proving settlement before a reminder are reconciled and excluded from the two-slice outcome chart. Monetary cards include all relevant posted invoices, not only reminded companies. Cash-receipt trends use actual payment dates across invoices.

Reports are operational and restatable when staff enter backdated corrections; they are not immutable accounting closes. Payment timing indicates sequence, not proof that a reminder caused payment.

## 7. Package the desktop

Build each installer on its native platform, or use the automatic [Windows and Ubuntu desktop workflow](https://github.com/iprabhatmishra25/Payment-Reminder-App/actions/workflows/desktop-release.yml).

Windows:

```powershell
py -3.12 scripts/desktop.py package-windows
```

Ubuntu:

```bash
python3 scripts/desktop.py package-ubuntu
```

Outputs are under `apps/desktop/release`: a per-user Windows NSIS installer, an Ubuntu Debian package, and an AppImage. The native workflow installs the Windows/Ubuntu packages and runs an actual Electron smoke journey before exposing successful installer artifacts. Signing credentials are optional for CI test packages and required for trusted organizational distribution; none are embedded in source. macOS is outside this release's verified matrix.

The login screen's **Server** setting accepts an HTTPS server origin, or loopback HTTP for local development. It saves only that non-secret origin. An administrator can alternatively set `DUEWISE_API_URL` before launch. Remote HTTP URLs are rejected.

Tokens stay in main-process memory. Closing the app clears local credentials; server sessions also expire and can be revoked. Native access is limited to narrow IPC operations, safe CSV export, and server configuration; navigation/popups and renderer Node access are disabled.

## 8. Tests

Backend:

```bash
cd services/backend
../../.venv/bin/python -m pytest -q
```

Use a dedicated disposable PostgreSQL database to exercise PostgreSQL behavior:

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/duewise_test ../../.venv/bin/python -m pytest -q
```

**The test fixture drops and recreates tables. Never point `TEST_DATABASE_URL` at real data.** GitHub CI provisions a dedicated PostgreSQL service for this test job.

Desktop:

```bash
cd apps/desktop
npm run build
cd ../..
node tests/e2e/desktop.cjs
```

On a headless Linux CI machine, use `xvfb-run -a node tests/e2e/desktop.cjs`. Set `DUEWISE_TEST_PYTHON` if your Python virtual environment is elsewhere. Electron tests use a separate temporary demo database and verify login, dashboard, company creation, invoice creation, partial payment, reminder queueing, settings, and logout.

See `docs/VALIDATION.md` for actual results and verification limits.

## 9. Security and recovery operations

- Back up PostgreSQL and the encryption key securely; loss of the key makes enrolled MFA secrets unrecoverable.
- Database passwords, encryption keys, and email credentials stay on the server.
- Use least-privilege deployment/runtime database roles if your operating environment requires a separate migration role.
- Keep API/worker logs free of passwords, tokens, and message bodies.
- Reset a lost password through recovery email, or use the authenticated server operator CLI after verifying ownership.
- Lost authenticator recovery is an explicit server operator process in v1; recovery codes are not implemented. `python -m app.cli disable-mfa --email ...` requires an interactive confirmation, revokes sessions/challenges, and logs the action. Production requires re-enrollment.
- A viewer cannot export or write payment/contact data. Accounts staff cannot reverse receipts, manage users, or change schedules.
- No hard deletion of financial history is exposed. Corrections use reversal/void workflows and audit entries.

Backup/restore instructions are in `docs/OPERATIONS.md`. Test restoration before using real records. Keep sending disabled after restoration until provider history is reconciled.

## Project map

```text
apps/desktop/                React UI, Electron main and preload, build configuration
services/backend/app/        API, schemas, models, auth, services, jobs, email webhooks
services/backend/migrations/ Initial frozen schema migration
services/backend/tests/      Business, API, concurrency, auth, reminder and webhook tests
tests/e2e/                   Real Electron end-to-end journey
scripts/                     Configuration and local launcher
infra/                       Containers, shared services, HTTPS proxy
docs/                        Architecture, operations, actual validation results
```
