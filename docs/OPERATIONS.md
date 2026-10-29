# Deployment and recovery runbook

## Initial release

1. Use a clean PostgreSQL database; do not promote a demo database.
2. Generate and securely retain the database password and Fernet encryption key.
3. Deploy migrations once, then API, Redis, one scheduler, and workers.
4. Create the admin interactively and enroll MFA.
5. Configure HTTPS and a verified sender domain; pilot with a recipient allowlist.
6. Run a small manual preview and verify provider acceptance/delivery history.
7. Reconcile report counts with accounts staff; then enable the monthly policy.

## Monitor

- `/health/live` checks API liveness; `/health/ready` checks the database connection.
- Authenticated `GET /api/v1/operations/status` reports pending outbox work, failed/uncertain reminders, and worker heartbeat freshness.
- A stale worker heartbeat can mean scheduler, broker, or worker failure; inspect container logs.
- Monitor disk/DB growth, API latency/error rate, provider quota, bounce counts, and backup age outside the app.

The API deliberately ignores forwarded headers. With the supplied single reverse proxy, unauthenticated IP limits aggregate at the proxy address; per-account login limits still apply. For larger deployments, configure trusted proxy handling explicitly rather than trusting arbitrary forwarded headers.

## Backup

Example logical backup from the project root:

```bash
docker compose --env-file .env -f infra/compose.yml exec -T db pg_dump -U duewise -d duewise -Fc > duewise-backup.dump
```

Transfer the backup to encrypted, access-controlled storage. Back up the encryption key separately. Never include secrets or real database dumps in the source repository or public artifact packages.

Define a recovery-point objective with the business owner. Daily backups can lose up to a day of newly recorded receipts. Use database point-in-time recovery if that loss is unacceptable.

## Restore rehearsal

1. Restore into a separate PostgreSQL database, not over a running production database.
2. Set `EMAIL_MODE=preview`, disable the reminder policy, and stop the scheduler during verification.
3. Use `pg_restore --no-owner` with a role authorized to create the restored objects.
4. Check user counts, invoice counts, sums of valid payments, outstanding totals, reversal history, and pending work.
5. Reconcile reminder IDs/provider history. An older backup can lack messages that were already delivered.
6. Verify encryption-key access and sign-in, then test a preview run.
7. Re-enable sending only after the accounts owner/operator has reconciled the data.

## Uncertain reminder

1. An administrator opens Reminders and selects Review on an UNKNOWN message.
2. Check the provider history independently using recipient/time and provider message identifiers.
3. If accepted, enter the real acceptance time and evidence; the report can then count the send.
4. If definitively not sent, record that evidence. The message becomes eligible for a manual retry.
5. Never use a new monthly identity merely to force another send after a timeout.

## Password/MFA recovery

Normal password resets use a single-use email token with a 20-minute lifetime and revoke current sessions. If email delivery is unavailable, a verified operator can run the CLI reset command interactively. Lost MFA recovery requires independent identity verification, interactive `disable-mfa`, and production re-enrollment. No MFA recovery codes are implemented in this release.

## Upgrades and rollback

Keep immutable server image versions and previous desktop installers. Back up before schema changes and make additive migrations where possible. Do not change the frozen initial migration; add a new revision. Desktop version 0.1.0 uses API v1. Check compatibility before changing response contracts.

Rolling back a server image is different from restoring a database. Restoring a backup may discard payments entered after it was created. Treat restoration as an explicit incident-recovery operation with reconciliation.

## Platform distribution

Test Windows packages on Windows, macOS packages on macOS, and Linux packages on supported Linux distributions. Production signing/notarization credentials and automatic update hosting are operator responsibilities. The app contains no private signing keys and does not automatically install downloaded updates.
