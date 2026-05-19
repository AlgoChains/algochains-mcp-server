# waitlist-slack-notify

Posts real waitlist signups from Supabase Database Webhooks to Slack channel
`#sign-up-waitlist-priority-focus` (`C0A075W6D4H`).

## Production tables discovered

Read-only PostgREST discovery against project `trkpzsnwjtmvgppuzlwu` found:

- `home_betawaitlist` — active beta waitlist, 15 rows, latest row on 2026-05-12 UTC
- `home_waitlist` — legacy waitlist, 3 rows, latest row on 2026-02-09 UTC
- `algochains_waitlist` — not found in this project

Create Database Webhooks on `INSERT` for:

1. `home_betawaitlist`
2. `home_waitlist` only if legacy signups can still arrive there

## Secrets

Set these in the Supabase project before deploying:

```bash
supabase secrets set \
  SLACK_BOT_TOKEN="xoxb-..." \
  SLACK_CHANNEL_ID="C0A075W6D4H" \
  WAITLIST_NOTIFY_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(24))')" \
  --project-ref trkpzsnwjtmvgppuzlwu
```

Use the same `WAITLIST_NOTIFY_SECRET` value in each Database Webhook's
`x-waitlist-secret` header.

## Deploy

```bash
supabase functions deploy waitlist-slack-notify \
  --project-ref trkpzsnwjtmvgppuzlwu \
  --no-verify-jwt
```

The repository also sets `verify_jwt = false` for this function in
`supabase/config.toml` because Supabase Database Webhooks authenticate with the
shared `x-waitlist-secret` header.

## Database Webhook settings

For each live table:

- Event: `INSERT`
- Method: `POST`
- URL: `https://trkpzsnwjtmvgppuzlwu.supabase.co/functions/v1/waitlist-slack-notify`
- Timeout: `5000` ms
- Headers:
  - `Content-Type: application/json`
  - `x-waitlist-secret: <WAITLIST_NOTIFY_SECRET>`

## Anti-spam layers

1. Database-level unique indexes on lowercased email via
   `20260512061500_waitlist_slack_constraints.sql`.
2. Test-email and status filtering in the Edge Function.
3. Shared secret header before parsing business logic.
4. Ten-minute in-memory record ID dedup for warm Edge Function instances.
5. Slack `429` retry using the `Retry-After` header.

## Smoke tests

After deployment and webhook setup:

```sql
INSERT INTO home_betawaitlist (email, name, notes, source, status)
VALUES ('smoke@algochains.ai', 'Smoke Test', 'Testing Slack notify pipeline', 'manual-smoke', 'waiting');
```

Expected: one Block Kit message in Slack within 5 seconds.

Then test duplicate and filtering:

```sql
-- Should fail once the unique index migration has been applied.
INSERT INTO home_betawaitlist (email, name, status)
VALUES ('smoke@algochains.ai', 'Smoke Again', 'waiting');

-- Should be filtered by the Edge Function and not post to Slack.
INSERT INTO home_betawaitlist (email, name, status)
VALUES ('test@test.com', 'Filtered Test', 'waiting');
```

Clean up:

```sql
DELETE FROM home_betawaitlist
WHERE email IN ('smoke@algochains.ai', 'test@test.com');
```
