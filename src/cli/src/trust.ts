/**
 * AlgoChains CLI — Trust Ladder + Kill Switch
 *
 * Trust tiers (aligned with plan):
 *   T0 — READ:         discover-tools, detect-market-regime, get-bot-health, market data
 *   T1 — COMPUTE:      run-backtest, optimize-strategy, validate-strategy, dispatch-tower-job
 *   T2 — PAPER:        place-order (paper broker), subscribe-to-bot (paper)
 *   T3 — LIVE:         place-order (live), flatten-position, restart-bot, close-all-positions
 *
 * Kill switch: touch ~/.algochains/KILLSWITCH → blocks ALL T2/T3 operations immediately.
 * Audit log:   every T2/T3 action appended to ~/.algochains/audit.jsonl (append-only).
 */
import { appendFileSync, existsSync, readFileSync, writeFileSync } from "fs";
import { AUDIT_FILE, KILLSWITCH_FILE } from "./config.js";

export type TrustTier = "T0" | "T1" | "T2" | "T3";

const TIER_MAP: Record<string, TrustTier> = {
  // T0 — always allowed
  "discover-tools": "T0",
  "get-tool-details": "T0",
  "execute-dynamic-tool": "T0",
  "detect-market-regime": "T0",
  "get-bot-health": "T0",
  "browse-strategy-marketplace": "T0",
  "portfolio-summary": "T0",
  "get-positions": "T0",
  "get-account": "T0",
  "get-orders": "T0",
  "onyx-ask": "T0",
  "onyx-search": "T0",
  "graphiti-search": "T0",
  "get-quote": "T0",
  "get-market-data": "T0",
  "massive-search-endpoints": "T0",
  "get-fills": "T0",
  "get-platform-health": "T0",
  "broker-health-check": "T0",
  "check-risk-alerts": "T0",

  // T1 — compute (allowed with --confirm or profile setting)
  "run-backtest": "T1",
  "optimize-strategy": "T1",
  "validate-strategy": "T1",
  "walk-forward-test": "T1",
  "dispatch-tower-job": "T1",
  "dispatch-gpu-task": "T1",
  "massive-call-api": "T1",
  "massive-query-data": "T1",
  "massive-run-pipeline": "T1",
  "run-evolution-cycle": "T1",
  "backtest-strategy": "T1",

  // T2 — paper execution
  "place-order": "T2",           // broker determines live vs paper
  "subscribe-to-bot": "T2",
  "create-shadow-portfolio": "T2",
  "execute-intent": "T2",

  // T3 — live destructive
  "flatten-position": "T3",
  "close-position": "T3",
  "close-all-positions": "T3",
  "cancel-order": "T3",
  "cancel-all-orders": "T3",
  "restart-bot": "T3",
  "deploy-strategy": "T3",
  "activate-kill-switch": "T3",
};

export function getTier(command: string): TrustTier {
  const normalized = command.replace(/_/g, "-").toLowerCase();
  return TIER_MAP[normalized] ?? "T0";
}

// ── Kill switch ────────────────────────────────────────────────────────────────
export function isKillSwitchActive(): boolean {
  return existsSync(KILLSWITCH_FILE);
}

export function enableKillSwitch(reason?: string): void {
  const content = JSON.stringify({
    activated_at: new Date().toISOString(),
    reason: reason ?? "manual",
  });
  writeFileSync(KILLSWITCH_FILE, content, { mode: 0o600 });
}

export function disableKillSwitch(): void {
  if (existsSync(KILLSWITCH_FILE)) {
    const { unlinkSync } = require("fs") as typeof import("fs");
    unlinkSync(KILLSWITCH_FILE);
  }
}

export function readKillSwitchState(): { active: boolean; reason?: string; activated_at?: string } {
  if (!existsSync(KILLSWITCH_FILE)) return { active: false };
  try {
    const data = JSON.parse(readFileSync(KILLSWITCH_FILE, "utf-8"));
    return { active: true, ...data };
  } catch {
    return { active: true };
  }
}

// ── Gate check ────────────────────────────────────────────────────────────────
export interface GateOptions {
  command: string;
  dryRun?: boolean;
  safeOnly?: boolean;
  confirm?: boolean;
  profile?: "demo" | "paper" | "live";
}

export type GateResult =
  | { allowed: true }
  | { allowed: false; reason: string; hint: string };

export function checkTrustGate(opts: GateOptions): GateResult {
  const tier = getTier(opts.command);

  // Kill switch blocks T2/T3
  if ((tier === "T2" || tier === "T3") && isKillSwitchActive()) {
    const state = readKillSwitchState();
    return {
      allowed: false,
      reason: `Kill switch is ACTIVE (activated ${state.activated_at ?? "unknown"})`,
      hint: "Run: algochains killswitch off   to resume",
    };
  }

  // --safe-only blocks T2/T3
  if (opts.safeOnly && (tier === "T2" || tier === "T3")) {
    return {
      allowed: false,
      reason: `--safe-only blocks ${tier} tool '${opts.command}'`,
      hint: "Remove --safe-only to execute trading operations",
    };
  }

  // --dry-run blocks T2/T3 (but shows preview)
  if (opts.dryRun && (tier === "T2" || tier === "T3")) {
    return {
      allowed: false,
      reason: `DRY-RUN: would execute T${tier.slice(1)} tool '${opts.command}'`,
      hint: "Remove --dry-run to execute",
    };
  }

  // T3 requires --confirm
  if (tier === "T3" && !opts.confirm) {
    return {
      allowed: false,
      reason: `'${opts.command}' is T3 (LIVE) — requires --confirm flag`,
      hint: `algochains ${opts.command} --confirm`,
    };
  }

  // Demo profile blocks T2/T3
  if (opts.profile === "demo" && (tier === "T2" || tier === "T3")) {
    return {
      allowed: false,
      reason: "Demo profile does not allow trade execution",
      hint: "Switch profile: algochains --profile paper  or  algochains --profile live",
    };
  }

  return { allowed: true };
}

// ── Audit log ─────────────────────────────────────────────────────────────────
export interface AuditEntry {
  ts: string;
  tier: TrustTier;
  tool: string;
  args?: Record<string, unknown>;
  profile?: string;
  result: "success" | "blocked" | "error" | "dry_run";
  reason?: string;
  fill_id?: string;
  order_id?: string;
  duration_ms?: number;
}

export function appendAuditLog(entry: AuditEntry): void {
  const line = JSON.stringify(entry) + "\n";
  appendFileSync(AUDIT_FILE, line, { mode: 0o600 });
}

export function* readAuditLog(limit = 100): Generator<AuditEntry> {
  if (!existsSync(AUDIT_FILE)) return;
  const lines = readFileSync(AUDIT_FILE, "utf-8").trim().split("\n");
  const recent = lines.slice(-limit);
  for (const line of recent) {
    try { yield JSON.parse(line); } catch { /* skip malformed */ }
  }
}
