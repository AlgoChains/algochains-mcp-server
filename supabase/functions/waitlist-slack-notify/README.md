# waitlist-slack-notify

Posts real waitlist signups from Supabase Database Webhooks to Slack channel
`#sign-up-waitlist-priority-focus`.

## Production tables

Create Database Webhooks on `INSERT` for:

1. `home_betawaitlist`
2. `home_waitlist` (legacy signups)

## Secrets

Set these in the Supabase project before deploying (replace `<project-ref>` with
your own Supabase project reference):

```bash
supabase secrets set \
  SLACK_BOT_TOKEN="xoxb-..." \
  SLACK_CHANNEL_ID="<your-slack-channel-id>" \
  WAITLIST_NOTIFY_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(24))')" \
  --project-ref <project-ref>
```

Use the same `WAITLIST_NOTIFY_SECRET` value in each Database Webhook's
`x-waitlist-secret` header.

## Deploy

```bash
supabase functions deploy waitlist-slack-notify \
  --project-ref <project-ref> \
  --no-verify-jwt
```

The repository also sets `verify_jwt = false` for this function in
`supabase/config.toml` because Supabase Database Webhooks authenticate with the
shared `x-waitlist-secret` header.

## Database Webhook settings

For each live table:

- Event: `INSERT`
- Method: `POST`
- URL: `https://<project-ref>.supabase.co/functions/v1/waitlist-slack-notify`
- Timeout: `5000` ms
- Headers:
  - `Content-Type: application/json`
  - `x-waitlist-secret: <WAITLIST_NOTIFY_SECRET>`

## Anti-spam layers

1. Database-level unique indexes on lowercased email
2. Test-domain filtering in `waitlist_notify.ts`
3. Shared secret header verification (`x-waitlist-secret`)
