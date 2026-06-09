/**
 * algochains doctor — Pre-flight health check
 * Runs all checks in parallel and streams results.
 * algochains doctor [--fix] [--quick] [--json]
 */
import { execSync, spawnSync } from "child_process";
import { getActiveProfile, loadConfig } from "../config.js";
import { createMcpClient } from "../mcp_client.js";
import { isKillSwitchActive, readKillSwitchState } from "../trust.js";
import { retrieveCredential } from "../auth.js";

export interface CheckResult {
  name: string;
  status: "ok" | "warn" | "fail" | "skip";
  message: string;
  fix?: string;
}

const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED = "\x1b[31m";
const CYAN = "\x1b[36m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";

const TICK = `${GREEN}✓${RESET}`;
const CROSS = `${RED}✗${RESET}`;
const WARN_SYM = `${YELLOW}⚠${RESET}`;
const SKIP_SYM = `${DIM}–${RESET}`;

export async function runDoctorCheck(profileName?: string, quick = false, fix = false): Promise<CheckResult[]> {
  const config = loadConfig();
  const profile = getActiveProfile(profileName, config);
  const bridgeUrl = profile.mcp_bridge_url ?? config.mcp.bridge_url;
  const mcp = createMcpClient(bridgeUrl, 5_000);
  const results: CheckResult[] = [];

  // Run checks in parallel batches
  const checks: Array<() => Promise<CheckResult>> = [
    checkNodeVersion,
    checkPythonVersion,
    checkMcpCommand,
    () => checkMcpBridge(bridgeUrl),
    checkKillSwitch,
    () => checkTradovateAuth(profileName),
    () => checkAlpacaAuth(profileName),
    checkPolygonKey,
    checkDailyLossLimit,
    checkMarketSession,
    () => checkOnyxReachable(profile),
    () => checkGraphitiReachable(),
    checkTowerSSH,
    checkDiskSpace,
  ];

  if (quick) {
    // Quick mode: only the most critical checks
    const quickChecks = [checkNodeVersion, () => checkMcpBridge(bridgeUrl), checkKillSwitch];
    for (const check of quickChecks) {
      const result = await check().catch(e => ({
        name: "unknown",
        status: "fail" as const,
        message: String(e),
      }));
      results.push(result);
    }
    return results;
  }

  // Full parallel run
  const settled = await Promise.allSettled(checks.map(c => c()));
  for (const s of settled) {
    if (s.status === "fulfilled") results.push(s.value);
    else results.push({ name: "unknown", status: "fail", message: String(s.reason) });
  }

  // Auto-fix pass
  if (fix) {
    for (const result of results.filter(r => r.status === "fail" && r.fix)) {
      console.log(`\n  Attempting fix for: ${result.name}`);
      try {
        execSync(result.fix!, { stdio: "inherit" });
      } catch {
        console.log(`  ${CROSS} Fix failed`);
      }
    }
  }

  return results;
}

export function printDoctorResults(results: CheckResult[]): void {
  const maxName = Math.max(...results.map(r => r.name.length), 20);
  console.log(`\n${BOLD}AlgoChains Doctor${RESET}`);
  console.log("─".repeat(maxName + 40));

  for (const r of results) {
    const sym = r.status === "ok" ? TICK : r.status === "warn" ? WARN_SYM : r.status === "skip" ? SKIP_SYM : CROSS;
    const nameStr = r.name.padEnd(maxName + 2);
    console.log(`  ${sym}  ${nameStr}${r.message}`);
    if (r.fix && (r.status === "fail" || r.status === "warn")) {
      console.log(`       ${DIM}Fix: ${r.fix}${RESET}`);
    }
  }

  const ok = results.filter(r => r.status === "ok").length;
  const warn = results.filter(r => r.status === "warn").length;
  const fail = results.filter(r => r.status === "fail").length;

  console.log("─".repeat(maxName + 40));
  if (fail === 0 && warn === 0) {
    console.log(`  ${TICK}  ${GREEN}All ${ok} checks passed${RESET}`);
  } else {
    console.log(`  ${CYAN}${ok} ok${RESET}  ${YELLOW}${warn} warn${RESET}  ${RED}${fail} fail${RESET}`);
    if (fail > 0) console.log(`  Run ${CYAN}algochains doctor --fix${RESET} to attempt auto-fixes`);
  }
  console.log("");
}

// ── Individual checks ─────────────────────────────────────────────────────────

async function checkNodeVersion(): Promise<CheckResult> {
  try {
    const out = execSync("node --version", { encoding: "utf-8" }).trim();
    const ver = parseInt(out.replace("v", "").split(".")[0], 10);
    if (ver >= 18) return { name: "Node.js", status: "ok", message: `${out} (≥18 required)` };
    return { name: "Node.js", status: "fail", message: `${out} — need ≥18`, fix: "https://nodejs.org" };
  } catch {
    return { name: "Node.js", status: "fail", message: "not found", fix: "Install from https://nodejs.org" };
  }
}

async function checkPythonVersion(): Promise<CheckResult> {
  for (const cmd of ["python3", "python"]) {
    try {
      const out = execSync(`${cmd} --version`, { encoding: "utf-8" }).trim();
      const [, maj, min] = out.match(/(\d+)\.(\d+)/) ?? [];
      if (parseInt(maj, 10) >= 3 && parseInt(min, 10) >= 11)
        return { name: "Python", status: "ok", message: `${out} (≥3.11 required)` };
      return { name: "Python", status: "warn", message: `${out} — recommend 3.11+` };
    } catch { /* try next */ }
  }
  return { name: "Python", status: "warn", message: "not found (only needed for MCP server)", fix: "https://python.org" };
}

async function checkMcpCommand(): Promise<CheckResult> {
  try {
    execSync("algochains-mcp --version 2>/dev/null || algochains-mcp --help", { stdio: "ignore" });
    return { name: "algochains-mcp", status: "ok", message: "installed (pip entry point)" };
  } catch {
    return {
      name: "algochains-mcp",
      status: "fail",
      message: "not found",
      fix: "pip install algochains-mcp-server",
    };
  }
}

async function checkMcpBridge(bridgeUrl: string): Promise<CheckResult> {
  const start = Date.now();
  try {
    const res = await fetch(`${bridgeUrl}/health`, { signal: AbortSignal.timeout(3_000) });
    const ms = Date.now() - start;
    if (res.ok) return { name: "MCP HTTP bridge", status: "ok", message: `${bridgeUrl} — ${ms}ms` };
    return { name: "MCP HTTP bridge", status: "warn", message: `${bridgeUrl} returned ${res.status}` };
  } catch {
    return {
      name: "MCP HTTP bridge",
      status: "warn",
      message: `${bridgeUrl} unreachable (start with: algochains daemon start)`,
      fix: "algochains daemon start",
    };
  }
}

async function checkKillSwitch(): Promise<CheckResult> {
  if (isKillSwitchActive()) {
    const state = readKillSwitchState();
    return {
      name: "Kill switch",
      status: "warn",
      message: `ACTIVE — T3/live ops blocked (since ${state.activated_at ?? "unknown"})`,
      fix: "algochains killswitch off",
    };
  }
  return { name: "Kill switch", status: "ok", message: "inactive (trading allowed)" };
}

async function checkTradovateAuth(profileName?: string): Promise<CheckResult> {
  const cid = await retrieveCredential("tradovate", "cid");
  if (!cid) {
    return {
      name: "Tradovate auth",
      status: "warn",
      message: "credentials not set",
      fix: "algochains auth set tradovate",
    };
  }
  return { name: "Tradovate auth", status: "ok", message: "credentials found" };
}

async function checkAlpacaAuth(profileName?: string): Promise<CheckResult> {
  const key = await retrieveCredential("alpaca", "api_key");
  if (!key) {
    return {
      name: "Alpaca auth",
      status: "warn",
      message: "credentials not set (free paper trading available)",
      fix: "algochains auth set alpaca",
    };
  }
  return { name: "Alpaca auth", status: "ok", message: "credentials found" };
}

async function checkPolygonKey(): Promise<CheckResult> {
  const key = await retrieveCredential("polygon", "api_key");
  if (!key && !process.env.POLYGON_API_KEY) {
    return {
      name: "Polygon data",
      status: "warn",
      message: "no API key — market data may be limited",
      fix: "algochains auth set polygon",
    };
  }
  return { name: "Polygon data", status: "ok", message: "API key configured" };
}

async function checkDailyLossLimit(): Promise<CheckResult> {
  // Check if state/live_pnl.json exists and what daily PnL looks like
  const stateFile = process.env.ALGOCHAINS_CONTROL_TOWER
    ? `${process.env.ALGOCHAINS_CONTROL_TOWER}/state/live_pnl.json`
    : `${process.env.HOME}/CascadeProjects/algochains-control-tower/state/live_pnl.json`;
  try {
    const { readFileSync, existsSync } = await import("fs");
    if (!existsSync(stateFile)) return { name: "Daily loss limit", status: "skip", message: "state file not found" };
    const data = JSON.parse(readFileSync(stateFile, "utf-8"));
    const pnl = data.daily_pnl ?? 0;
    if (pnl <= -450) {
      return { name: "Daily loss limit", status: "fail", message: `Daily P&L: $${pnl.toFixed(2)} — near $500 limit` };
    }
    return { name: "Daily loss limit", status: "ok", message: `Daily P&L: $${pnl.toFixed(2)}` };
  } catch {
    return { name: "Daily loss limit", status: "skip", message: "cannot read state (run from control tower)" };
  }
}

async function checkMarketSession(): Promise<CheckResult> {
  const now = new Date();
  const et = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hour12: false }).formatToParts(now);
  const hour = parseInt(et.find(p => p.type === "hour")?.value ?? "0", 10);
  const min = parseInt(et.find(p => p.type === "minute")?.value ?? "0", 10);
  const day = now.toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "short" });
  const totalMin = hour * 60 + min;
  const isWeekday = !["Sat", "Sun"].includes(day);

  let session: string;
  let status: CheckResult["status"] = "ok";

  if (!isWeekday) {
    session = "closed (weekend)"; status = "warn";
  } else if (totalMin >= 9 * 60 + 30 && totalMin < 16 * 60) {
    session = "regular session (NYSE open)";
  } else if (totalMin >= 4 * 60 && totalMin < 9 * 60 + 30) {
    session = "pre-market";
  } else if (totalMin >= 16 * 60 && totalMin < 20 * 60) {
    session = "after-hours";
  } else {
    session = "closed"; status = "warn";
  }

  return { name: "Market session", status, message: `${session} (${day} ${hour}:${String(min).padStart(2, "0")} ET)` };
}

async function checkOnyxReachable(profile: { mcp_bridge_url?: string }): Promise<CheckResult> {
  const onyxUrl = process.env.ONYX_API_URL ?? "http://100.89.114.31:8085";
  try {
    const res = await fetch(`${onyxUrl}/api/health`, { signal: AbortSignal.timeout(3_000) });
    if (res.ok) return { name: "Onyx RAG", status: "ok", message: `${onyxUrl} — healthy` };
    return { name: "Onyx RAG", status: "warn", message: `${onyxUrl} — unhealthy (${res.status})` };
  } catch {
    return { name: "Onyx RAG", status: "warn", message: `${onyxUrl} unreachable (tower may be off)` };
  }
}

async function checkGraphitiReachable(): Promise<CheckResult> {
  try {
    // Try bolt connection (TCP check only)
    const net = await import("net");
    await new Promise<void>((resolve, reject) => {
      const socket = net.createConnection({ host: "localhost", port: 7687, timeout: 2000 });
      socket.on("connect", () => { socket.destroy(); resolve(); });
      socket.on("error", reject);
      socket.on("timeout", () => { socket.destroy(); reject(new Error("timeout")); });
    });
    return { name: "Graphiti / Neo4j", status: "ok", message: "bolt://localhost:7687 reachable" };
  } catch {
    return {
      name: "Graphiti / Neo4j",
      status: "warn",
      message: "bolt://localhost:7687 unreachable",
      fix: "bash scripts/setup_graphiti_env.sh",
    };
  }
}

async function checkTowerSSH(): Promise<CheckResult> {
  const towerHost = process.env.ALGOCHAINS_TOWER_HOST ?? "100.89.114.31";
  try {
    const result = spawnSync("ssh", ["-o", "ConnectTimeout=3", "-o", "BatchMode=yes", towerHost, "echo ok"], {
      timeout: 4_000, encoding: "utf-8",
    });
    if (result.stdout?.trim() === "ok") {
      return { name: "Desktop tower (SSH)", status: "ok", message: `${towerHost} reachable` };
    }
    return { name: "Desktop tower (SSH)", status: "warn", message: `${towerHost} — SSH check failed` };
  } catch {
    return { name: "Desktop tower (SSH)", status: "warn", message: `${towerHost} unreachable (GPU tasks may fail)` };
  }
}

async function checkDiskSpace(): Promise<CheckResult> {
  try {
    const out = execSync("df -h ~/.algochains 2>/dev/null || df -h /tmp", { encoding: "utf-8" }).trim();
    const lines = out.split("\n");
    const dataLine = lines[1] ?? "";
    const parts = dataLine.trim().split(/\s+/);
    const usePct = parseInt(parts[4] ?? "0", 10);
    if (usePct >= 95) {
      return { name: "Disk space", status: "fail", message: `${usePct}% full — free space urgently needed` };
    }
    if (usePct >= 85) {
      return { name: "Disk space", status: "warn", message: `${usePct}% full` };
    }
    return { name: "Disk space", status: "ok", message: `${usePct}% used` };
  } catch {
    return { name: "Disk space", status: "skip", message: "could not check" };
  }
}
