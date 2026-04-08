# AlgoClaw — AlgoChains Agent Skill System

AlgoClaw is the subscriber-facing autonomous agent skill layer embedded in the AlgoChains MCP server.
Think of it as OpenClaw, but purpose-built for algorithmic trading.

## Quick Start

```bash
# List all skills
python algoclaw/cli.py --list

# Run a skill
python algoclaw/cli.py bot-health
python algoclaw/cli.py prop-fund-check
python algoclaw/cli.py position-size --param symbol=MNQ entry=18050 stop=17990 capital=50000

# Check system status
python algoclaw/cli.py --status

# Start daemon (scheduled skills)
python algoclaw/cli.py --daemon &
```

## From AI (Claude / Cursor)

```
"Run bot-health AlgoClaw skill"      → run_algoclaw_skill("bot-health")
"List all AlgoClaw skills"           → list_algoclaw_skills()
"AlgoClaw status"                    → get_algoclaw_status()
```

## Skill Tiers

| Tier | Category | Requires Owner? |
|------|----------|----------------|
| 0 | Daily essentials (safe, no live money) | No |
| 1 | Research & validation | No |
| 2 | Prop fund pipeline | No |
| 3 | Emergency / destructive | YES |
| 4 | Marketplace | No |

## Structure

```
algoclaw/
├── README.md          ← You are here
├── SKILL_INDEX.md     ← Full skill catalog (auto-generated)
├── cli.py             ← Entry point
├── skills/            ← Skill definitions (SKILL.md per skill)
├── agents/            ← Agent configs
├── cron/              ← Scheduled run config
├── memory/            ← Persistent agent memory
└── state/             ← Runtime state + audit log
```

## Full Blueprint

See: `blueprints/ALGOCLAW_BLUEPRINT.md` in the algochains-control-tower repo.
