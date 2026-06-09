# AlgoChains CLI — Gap Analysis & Subcommand Roadmap

**Last updated:** 2026-06-09 (v22.4.1)
**Scope:** the `algochains` TypeScript CLI (`src/cli/`) and the `algochains-mcp` Python entry point.

This is the file the README refers to for the "missing subcommands roadmap". It tracks
what the CLI can do today and what is planned, so the gap stays visible instead of
living in tribal memory.

---

## Current surface (v22.4.1)

### `algochains` (TypeScript CLI, React+Ink REPL when run bare)

| Command | Subcommands | Purpose |
|---------|------------|---------|
| `doctor` | — | 14 parallel health checks (env, keyring, daemon, MCP reachability) |
| `auth` | `set` / `list` / `rotate` / `clear` / `test` | Broker + API credentials in the OS keyring |
| `daemon` | `start` / `stop` / `status` / `logs` / `install` / `uninstall` | Background Hono SSE daemon on `localhost:39337` |
| `killswitch` | `on` / `off` / `status` | Emergency stop — blocks all T2/T3 operations (`~/.algochains/KILLSWITCH`) |
| `audit` | `tail` / `json` | Append-only T2/T3 audit log (`~/.algochains/audit.jsonl`) |
| `completion` | `<shell>` | bash / zsh / fish / PowerShell completions |
| `plugin` | `install` / `list` / `remove` / `update` | Manifest-based plugins with env isolation |
| `trigger` | `add` / `list` / `enable` / `disable` / `remove` | cron / watch / webhook / datetime automations |
| `config` | `init` / `show` / `generate <target>` | CLI config + IDE config generation (delegates to `quickstart.py`) |
| `<tool-name>` | (pass-through) | Any MCP tool by kebab-case name, trust-ladder gated |

### `algochains-mcp` (Python entry point)

| Flag | Purpose |
|------|---------|
| `--version` | Print installed package version |
| `--generate-config {cursor,claude-desktop,windsurf,claude-code,all}` | Write IDE MCP configs |
| `--request-access <email>` | Post a Slack subscription-approval request |
| `--demo-signal` | Inject a 15-second-TTL test signal for paper-fill verification |

---

## Missing subcommands (roadmap)

Ordered by expected operator value. None of these block the MCP tool pass-through —
every underlying tool is already callable as `algochains <tool-name>` — but dedicated
subcommands add argument validation, sane defaults, and human-readable output.

| # | Proposed command | Wraps | Why it matters | Status |
|---|-----------------|-------|----------------|--------|
| 1 | `algochains tower <dispatch\|status\|results>` | `dispatch_tower_job`, `get_tower_job_status` | GPU job dispatch is the most-used owner workflow not in the CLI (README "Desktop Tower Dispatch" section still shows a raw `python3 -c` snippet) | Planned |
| 2 | `algochains signals [--follow]` | `get_signal_stream`, `ack_signal` | Subscribers need a no-IDE way to watch copy-trade signals | Planned |
| 3 | `algochains pnl [--days N]` | `get_my_pnl`, `get_my_fills` | Subscriber paper/live P&L without an IDE | Planned |
| 4 | `algochains marketplace <browse\|show\|submit>` | `browse_strategy_marketplace`, `get_listing_detail`, `submit_to_marketplace` | Builder workflow currently IDE-only | Planned |
| 5 | `algochains bots [--bot MNQ]` | `get_bot_health`, `get_all_bot_ops_status` | One-command fleet health (today requires tool pass-through name knowledge) | Planned |
| 6 | `algochains backtest <run\|results>` | `run_builder_backtest`, `get_backtest_results` | Validation loop for strategy builders | Planned |
| 7 | `algochains validate <metrics.json>` | `validate_strategy_metrics` | Pre-submission gate check with file input | Planned |
| 8 | `algochains keys <request\|test>` | `--request-access`, bridge `/health` auth probe | Key lifecycle from the terminal; `test` verifies an `ac_live_`/`sub_live_` key against the hosted bridge | Planned |
| 9 | `algochains regime` | `detect_market_regime` | Most common single read; deserves a top-level alias | Planned |
| 10 | `algochains quickstart` | `scripts/quickstart.py` | Single binary entry for the wizard (today requires the Python script path) | Planned |

### Design constraints for all new subcommands

- Trust ladder applies unchanged: T0 reads need no confirmation; T1 needs profile or
  `--confirm`; T2/T3 stay kill-switch- and profile-gated. New subcommands must not
  introduce bypass paths.
- Output must be agent-friendly: `--json` flag on every subcommand, non-interactive by
  default, exit codes (0 ok / 1 degraded / 2 config error) matching
  `copy_trade_fanout_health.py` conventions.
- No new credentials surface: keys come from the OS keyring (`auth set`) or env, never
  flags (shell history leak).

---

## Known CLI gaps that are NOT subcommands

| Gap | Detail | Status |
|-----|--------|--------|
| Windows version display | Editable installs show stale metadata until `pip install -e .` re-runs | Documented in team handoff |
| `quickstart.py` flag parity | Lacks `--version` / `--request-access` / `--demo-signal` (those live on `algochains-mcp`) | Accepted — single source per flag |
| UX blueprint items | `UX_BLUEPRINT.md` tracks 14 onboarding/UX gaps (danger-tier surfacing, paper-mode banner, etc.) | Partially implemented |
