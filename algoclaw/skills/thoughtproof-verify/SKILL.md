# thoughtproof-verify

**Tier:** 2 (pre-trade verification — runs before any order execution)  
**Trigger:** Wired into place_order dispatch in server.py  
**MCP Tool:** `run_algoclaw_skill("thoughtproof-verify", {"claim":"Place MNQ long 2 @ market","stake":"high"})`  
**Source Pattern:** ThoughtProof/thoughtproof-mcp (cross-model adversarial attestation)

## What It Does

Before executing any order, routes the trade claim through 2–5 adversarial AI models for independent review.
Each model acts as a critic and returns ALLOW / BLOCK / UNCERTAIN with a signed attestation.

**Models used:**
- Fast (2 models, ~$0.008): Claude Haiku + GPT-4o-mini
- Standard (3 models, ~$0.03): + Gemini Flash
- Deep (5 models, ~$0.08): + Grok + DeepSeek

## Config

```python
THOUGHTPROOF_ENABLED = os.environ.get("THOUGHTPROOF_ENABLED", "false") == "true"
THOUGHTPROOF_MIN_STAKE = os.environ.get("THOUGHTPROOF_MIN_STAKE", "high")  # low/medium/high/critical
THOUGHTPROOF_SPEED = os.environ.get("THOUGHTPROOF_SPEED", "fast")  # fast/standard/deep
```

## Decision Logic

| Verdict | Outcome |
|---------|---------|
| All ALLOW | Execute trade |
| Any BLOCK | Refuse trade, log reason, alert |
| 2+ UNCERTAIN | Escalate to owner, hold trade |

## Output

```json
{
  "claim": "Place MNQ long 2 contracts at market — AI signal 87.1%",
  "verdict": "ALLOW",
  "models_consulted": 2,
  "speed": "fast",
  "cost_usd": 0.008,
  "attestation_id": "tp_a1b2c3d4",
  "reasons": ["Signal consistent with regime", "Risk/reward ratio acceptable"],
  "blocked": false
}
```

## Cost Justification

With $500/day max loss limit, $0.008 per order pre-verification costs < 0.01% of daily risk budget.
Blocking even one bad $200 loss covers 25,000 order verifications.
