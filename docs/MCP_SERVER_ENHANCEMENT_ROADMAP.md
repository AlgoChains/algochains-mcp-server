# AlgoChains MCP Server — Enhancement Roadmap

**Purpose:** Priority-ordered upgrades after the control-tower accuracy push. This roadmap focuses on the **algochains-mcp-server** package (`src/algochains_mcp/`), not the futures bots.

**Principles:** Real broker/data only; fail closed on secrets; read-only defaults for risky paths; align semantics with `algochains-control-tower` where both touch Tradovate, Supabase, or signal propagation.

---

## Tier P0 — Security, correctness, money safety

| # | Item | Why | Primary files / notes |
|---|------|-----|------------------------|
| 1 | **Purge hardcoded keys from tests** | `docs/GOTCHAS_AND_BUGS.md` flags `tests/test_live_audit.py` with embedded API keys — credential leak on clone. | Rotate exposed keys; load from env only; add `pytest` skip if env missing. |
| 2 | **Destructive-tool audit + CI gate** | SAFETY_MODEL describes layers; not every tool path may declare `ToolAnnotations` consistently or respect `tool_danger_tiers`. | `tool_manifest.py`, `tool_danger_tiers.py`, `tests/test_tool_manifest.py` — CI fails if a write-capable tool is marked `readOnly`. |
| 3 | **Trade propagation parity** | Control tower hardened `trade_propagation` to fail closed on missing secret; ensure server and examples never reintroduce defaults. | `trade_propagation.py`, `examples/trade_propagation/` |
| 4 | **Broker `place_order` contract tests** | Recent fixes (e.g. `orderId` falsy edge cases) show API response shape drift risk. | `brokers/tradovate.py`, golden JSON fixtures per Tradovate response variant. |
| 5 | **FIFO / P&L feeders — regression suite** | `prop_fund_data_feeder.py` timestamp handling was a correctness bug class. | Property tests on ordering, parse failures, empty inputs. |

---

## Tier P1 — Observability and operations

| # | Item | Why | Primary files / notes |
|---|------|-----|------------------------|
| 6 | **Structured tool telemetry** | `middleware.py` logs calls; add trace IDs, latency histograms, and error taxonomy (`AlgoChainsError` subclasses) for dashboards. | `middleware.py`, `errors.py`, optional OpenTelemetry exporter. |
| 7 | **Startup health ↔ runtime probe** | `scripts/startup_health_check.py` exists; expose a **read-only** MCP tool or HTTP probe returning JSON (version, lazy-load failures, broker ping). | `http_bridge.py` / `http_transport.py`, `server.py` |
| 8 | **Rate limiter metrics** | `per_tool_rate_limiter.py` — export counters (rejects, waits) for incident triage. | Same pattern as control-tower Supabase caps. |
| 9 | **Credential vault audit log** | `credential_vault.py` — append-only audit of *which tool* requested *which* secret handle (never log values). | Compliance path for team expansion. |

---

## Tier P2 — Reliability and performance

| # | Item | Why | Primary files / notes |
|---|------|-----|------------------------|
| 10 | **Tradovate session lifecycle** | `_ensure_token` / reconnect paths: ensure no double `connect()`, stale `httpx` client, or unbounded retry storms. | `brokers/tradovate.py` — mirror control-tower WS + REST discipline. |
| 11 | **Lazy import failure surfacing** | `_lazy_import` returns `None` quietly; surface a **single** `get_server_diagnostics` tool listing failed modules. | `server.py` |
| 12 | **SSE / HTTP transport hardening** | Long-lived connections need heartbeat, max message size, and graceful degradation when Cloudflare Access blocks curl. | `sse_server.py`, `http_transport.py` |
| 13 | **Marketplace validator timeouts** | `marketplace/validator.py`, `bridge.py` — bounded time per strategy; cancel stale work. | Prevents agent sessions from hanging. |

---

## Tier P3 — Developer experience and quality

| # | Item | Why | Primary files / notes |
|---|------|-----|------------------------|
| 14 | **Contract tests per broker** | Schwab, E*TRADE, Rithmic, Alpaca connectors vary; smoke tests with recorded responses (no live keys in CI). | `brokers/*_connector.py`, VCR or fixture dir. |
| 15 | **Document smart vs full mode** | `server.py` docstring vs README drift confuses agents. | Single `VERSION` / tool count source (already partially fixed per GOTCHAS). |
| 16 | **Algoclaw skill parity** | `algoclaw/skills/` — ensure each skill maps to a real tool name in the manifest (no dead references). | `skills_registry.py`, `tool_manifest.py` |

---

## Tier P4 — Product and platform (revenue / scale)

| # | Item | Why | Primary files / notes |
|---|------|-----|------------------------|
| 17 | **Marketplace delivery webhooks** | Subscriber notify on listing change; signed payloads; retry with idempotency keys. | `marketplace/supabase_tools.py`, Django/backend contract. |
| 18 | **Payout on realized P&L** | Not paper — tie to Rithmic/Tradovate fills via existing feeder patterns. | `prop_fund_data_feeder.py`, `prop_fund_manager.py` |
| 19 | **Multi-tenant isolation** | `cloud_saas/tenant_middleware.py` — enforce tenant ID on every tool that touches user data. | Prevents cross-tenant data leaks at scale. |
| 20 | **Onyx / RAG freshness** | `onyx_intelligence/onyx_client.py` — health + last-ingest timestamp in tool output. | Agents stop trusting stale semantic search. |

---

## Suggested sequencing (next 4 weeks)

1. **Week 1:** P0-1 (test secrets), P0-2 (destructive manifest CI), P0-4 (Tradovate orderId fixtures).  
2. **Week 2:** P1-6 (structured telemetry), P1-7 (diagnostics tool), P2-10 (Tradovate lifecycle).  
3. **Week 3:** P2-11 (lazy-import diagnostics), P3-14 (broker smoke fixtures).  
4. **Week 4:** P4-17 / P4-18 (marketplace webhooks + realized P&L hooks) as product priority dictates.

---

## Explicitly out of scope (unless owner approves)

- Changing live trading guardrail numeric limits in `trading_guardrails.py`.  
- Adding new order-placement surfaces without human-in-the-loop review for high notional.  
- Expanding “full mode” tool exposure without Cursor/client load testing.

---

## References

- `docs/GOTCHAS_AND_BUGS.md` — open P1 items (signal logger kwargs, test keys).  
- `SAFETY_MODEL.md` — circuit breakers and elicitation story.  
- `LATENCY_GUIDE.md` — performance expectations for tool design.  
- Control tower alignment: `algochains-control-tower` `tradovate_client.py`, `supabase_audit.py`, `trade_log` schema.
