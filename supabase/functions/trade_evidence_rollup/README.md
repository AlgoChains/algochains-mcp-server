# trade_evidence_rollup

Authenticated daily rollup for Trade Evidence Advisor summaries.

## Auth

Requires `x-rollup-secret` (or `Authorization: Bearer <secret>`) matching
Edge secret `TRADE_EVIDENCE_ROLLUP_SECRET`.

`verify_jwt` is currently off because pg_cron calls this with a shared secret
header rather than a user JWT.

## Deploy

```bash
supabase secrets set TRADE_EVIDENCE_ROLLUP_SECRET=<value> --project-ref trkpzsnwjtmvgppuzlwu
supabase functions deploy trade_evidence_rollup --project-ref trkpzsnwjtmvgppuzlwu --no-verify-jwt
```

## Cron

Job name: `trade_evidence_rollup_daily` (schedule `15 4 * * *`)  
Must include header `x-rollup-secret`.
