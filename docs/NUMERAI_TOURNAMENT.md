# Numerai Tournament Operator Runbook

This runbook covers the Numerai Classic tools registered by
`src/algochains_mcp/server.py` and implemented under
`src/algochains_mcp/tournament/numerai/`.

Use it when running the tournament pipeline through MCP or the local CLI. These
tools are isolated from futures bots: no futures modules are imported, model
artifacts are written under `state/numerai/models/`, and `NUMERAI_SECRET_KEY`
must never appear in logs or tool responses.

## Tool Sequence

| Step | MCP tool | Purpose | Side effects |
|------|----------|---------|--------------|
| 1 | `numerai_status` | Show config, env flags, dataset version, cadence notes | None |
| 2 | `numerai_round_info` | Read current round through `numerapi` | External API read |
| 3 | `numerai_download_dataset` | Download train/live parquet plus `features.json` | Writes under `ALGOCHAINS_STATE_DIR/numerai/data/` |
| 4 | `numerai_train_baseline` | Train the LightGBM baseline with era holdout and embargo | Writes `model_*.pkl` under `state/numerai/models/` |
| 5 | `numerai_validate_metrics` | Compute local per-era proxy metrics | None beyond dataset/model reads |
| 6 | `numerai_dry_run_submit` | Generate and validate a submission CSV without uploading | Writes under `state/numerai/submissions/` |
| 7 | `numerai_upload_predictions` | Upload the newest validated CSV | Remote irreversible submit; gated |
| 8 | `numerai_get_model_scores` | Read leaderboard/model scores through `numerapi` | External API read |

Run `numerai_dry_run_submit` before `numerai_upload_predictions`. Upload looks
for the newest CSV in the submissions directory and fails if no dry-run CSV
exists.

## Environment

| Variable | Required for | Notes |
|----------|--------------|-------|
| `ALGOCHAINS_STATE_DIR` | Optional for all tools | Defaults to repo `state/`; never uses `/tmp`. |
| `NUMERAI_PUBLIC_ID` | Round info, download, model scores, live upload | Read at call time by `config.py`. |
| `NUMERAI_SECRET_KEY` | Round info, download, model scores, live upload | Only boolean presence is logged or returned. |
| `NUMERAI_ALLOW_LIVE` | Live upload | Must be `1`, `true`, or `yes`; otherwise upload remains dry-run. |
| `NUMERAI_MODEL_ID` | Optional upload fallback | Tool calls can pass `model_id` explicitly instead. |
| `NUMERAI_SLACK_WEBHOOK` | Optional alerts | Used by monitoring helpers; falls back to `SLACK_WEBHOOK_URL`. |

Dataset defaults live in `src/algochains_mcp/tournament/numerai/config.py`:

- Dataset version: `v5.2`
- Feature set: `medium`
- Target column: `target_cyrus20`
- Holdout eras: `4`
- Embargo eras: `4`

## MCP Examples

Status is safe to call without Numerai credentials:

```json
{
  "tool": "numerai_status",
  "arguments": {}
}
```

Download the medium feature set:

```json
{
  "tool": "numerai_download_dataset",
  "arguments": {
    "feature_set": "medium",
    "force_redownload": false
  }
}
```

Train, validate, and generate a dry-run CSV:

```json
{
  "tool": "numerai_train_baseline",
  "arguments": {
    "feature_set": "medium",
    "holdout_n": 4,
    "embargo_eras": 4
  }
}
```

```json
{
  "tool": "numerai_validate_metrics",
  "arguments": {
    "neutralized": true
  }
}
```

```json
{
  "tool": "numerai_dry_run_submit",
  "arguments": {
    "neutralized": true
  }
}
```

Live upload requires all of the following:

- Owner HTTP bridge access; developer keys explicitly block this tool.
- `confirm=true` in tool arguments.
- `NUMERAI_ALLOW_LIVE=1` in the server environment.
- `NUMERAI_SECRET_KEY` configured.
- A `model_id` argument or configured `NUMERAI_MODEL_ID`.
- Optional `round_id` matching the current Numerai round.

```json
{
  "tool": "numerai_upload_predictions",
  "arguments": {
    "model_id": "NUMERAI_MODEL_UUID",
    "round_id": 999,
    "confirm": true
  }
}
```

If `confirm=false`, the tool returns `uploaded=false`. If
`NUMERAI_ALLOW_LIVE` is not enabled, `upload_predictions_gated()` returns a
dry-run result and does not call `napi.upload_predictions()`.

## CLI Examples

The same pipeline can run locally:

```bash
PYTHONPATH=src python3 -m algochains_mcp.tournament.numerai.run_pipeline --dry-run
PYTHONPATH=src python3 -m algochains_mcp.tournament.numerai.run_pipeline --train-only
NUMERAI_ALLOW_LIVE=1 PYTHONPATH=src python3 -m algochains_mcp.tournament.numerai.run_pipeline \
  --submit --model-id <uuid> --round-id <current_round>
```

The CLI prints a secret-filtered JSON result. If the run is incomplete it exits
non-zero.

## Safety And Scoring Constraints

- `numerai_upload_predictions` is `TIER_ORDER_EXEC` in
  `src/algochains_mcp/tool_danger_tiers.py` because tournament upload is
  irreversible.
- `numerai_download_dataset`, `numerai_train_baseline`, and
  `numerai_dry_run_submit` are `TIER_WRITE_LOCAL`.
- The upload path does not stake NMR. Staking remains a manual Numerai UI action.
- `build_submission()` validates live IDs, prediction range `[0, 1]`, non-zero
  standard deviation, and the `prediction` column before a CSV is considered
  upload-ready.
- Local validation metrics are labeled `proxy_corr` / `proxy_mmc`. They are not
  bit-identical to Numerai server scoring; leaderboard results from
  `numerai_get_model_scores` are authoritative after the scoring lag.
- `live.parquet` is re-downloaded for each round because live IDs change.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `NUMERAI_PUBLIC_ID and NUMERAI_SECRET_KEY must be set` | Credentials missing for a `numerapi` call | Set both env vars in the server process. |
| `No trained model found` | Validation or dry-run ran before training | Run `numerai_train_baseline` first. |
| `No submission CSV found` | Upload ran before dry-run | Run `numerai_dry_run_submit` first. |
| `confirm=false` response | Upload called without explicit confirmation | Re-run with `confirm=true` after checking the dry-run CSV. |
| `NUMERAI_ALLOW_LIVE not set` | Server is still in safe dry-run mode | Set `NUMERAI_ALLOW_LIVE=1` only for the intended live upload window. |
| `Round mismatch` | Provided `round_id` differs from Numerai current round | Re-download live data and regenerate predictions for the current round. |
