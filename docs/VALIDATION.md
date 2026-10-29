# Validation results — 29 October 2026

These are checks performed during implementation, not a claim that a production server has been deployed.

| Check | Observed result |
|---|---|
| Backend automated suite | 40 tests passed on a fresh SQLite database |
| TypeScript validation | Passed using `tsc --noEmit` |
| Production React build | Passed using Vite |
| Electron development end-to-end journey | Passed in the actual Electron main/preload/renderer runtime |
| Pie chart rendering | Two real SVG sectors verified, not only legend text |
| Packaged Linux runtime end-to-end journey | Passed using the packaged main/preload/renderer and bundled assets |
| Linux AppImage packaging | Built successfully for x64 |
| PostgreSQL migration SQL | Generated successfully with the PostgreSQL dialect |
| Deployment/workflow YAML | Parsed successfully |
| Local scale check | 10,000 companies, 120,000 invoices, 72,000 payments; balance assertions passed |
| Local monthly report time | Approximately 0.52–0.68 seconds across two successful disposable SQLite scale runs |

## End-to-end journey exercised

1. Log in to a fresh labeled demo workspace.
2. Load the dashboard, actual pie sectors, and company report.
3. Create a company through Electron IPC and FastAPI.
4. Create a posted invoice for that company.
5. Record a partial payment and verify the refreshed remaining balance/status.
6. Preview overdue reminders and queue a durable run.
7. Save a monthly schedule setting on the server.
8. Sign out; verify no uncaught renderer errors during the journey.

## Backend coverage

- Exact partial/full balances; zero, negative, precision, future timestamp, overpayment, and draft-payment rejection.
- Identical retry deduplication, conflicting request keys, and concurrent overpayment prevention.
- Reversal history, role permissions, login/session expiry, refresh rotation, logout revocation, and session ownership.
- TOTP enrollment, challenge verification, replay rejection, password reset single use, and account-enumeration-safe responses.
- Company-count attribution, partial payment pending, multiple invoices per company, historical cutoffs, backdated receipts, and empty cohorts.
- Preview side effects, monthly deduplication, post-queue payment cancellation, worker-crash/timeout uncertainty, explicit admin review, and confirmed provider-acceptance state.
- Calendar/timezone boundaries, scheduled-run uniqueness, signed event verification, expiration/tampering rejection, duplicate/out-of-order webhooks, and safe retries using refreshed configuration.
- CSV export permission checks and spreadsheet formula escaping.

## Verification limits

- The execution environment could not run PostgreSQL under its restricted single-user namespace. PostgreSQL runtime/concurrency tests are provided in GitHub CI and must pass on the target environment before production use. SQLite passing does not establish identical PostgreSQL behavior.
- Docker Compose was inspected and parsed, but Docker services were not launched here.
- No real email credentials were supplied. Live Resend delivery and real provider callbacks were not exercised; adapter outcomes and signatures were tested with controlled test data.
- Windows/macOS installers, code signing, notarization, backups/restoration on a real server, and actual monthly unattended operation need target-environment validation.
- Linux desktop QA used a virtual display; the test runner adds `--no-sandbox` only when running as root in that restricted test environment. The shipped application configuration retains renderer sandboxing and should be run as a normal user.
- One upstream Starlette/httpx deprecation warning was emitted by the backend test client. The production build also emitted a bundle-size advisory. Neither check failed.

## Before real use

Run the PostgreSQL CI job, configure the always-on server and sender domain, enroll staff MFA, test a recipient allowlist, verify HTTPS, sign/distribute target installers, rehearse restoration, and reconcile one pilot reminder cycle with accounts staff.

## Windows and Ubuntu release 0.1.1

The cross-platform workflow adds native Windows and Ubuntu 24.04 x64 jobs. Each installs Python/Node dependencies, runs portable launcher regression checks, tests the local backend, builds the native installers, installs the Windows/Ubuntu desktop package, and runs the real Electron payment/reminder journey. Successful installer artifacts are uploaded only after that platform's checks pass.

The original results above describe the initial 0.1.0 implementation. For current 0.1.1 results and downloads, use the [Windows and Ubuntu desktop workflow](https://github.com/iprabhatmishra25/Payment-Reminder-App/actions/workflows/desktop-release.yml). Its run result is the evidence of platform verification; workflow configuration alone is not a test result. The separate PostgreSQL CI job remains enabled.
