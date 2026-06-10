#!/usr/bin/env node
/**
 * AlgoChains CLI — Main entry point
 * Parallel prefetch fires before heavy imports (Claude Code pattern).
 */

// ── Parallel prefetch (before any heavy imports) ───────────────────────────────
import { CONFIG_DIR, DAEMON_PORT_FILE } from "./config.js";
import { existsSync } from "fs";

// Fire-and-forget: pre-warm daemon connection check and kill switch state
const prefetchPromises: Promise<unknown>[] = [
  // Check if daemon is running (saves latency for commands that need it)
  existsSync(DAEMON_PORT_FILE)
    ? import("fs").then(fs => {
        const port = fs.readFileSync(DAEMON_PORT_FILE, "utf-8").trim();
        return fetch(`http://localhost:${port}/health`, { signal: AbortSignal.timeout(1_000) }).catch(() => null);
      })
    : Promise.resolve(null),
];

// ── Heavy imports ──────────────────────────────────────────────────────────────
import { Command } from "commander";
import { render } from "ink";
import React from "react";

import { runDoctorCheck, printDoctorResults } from "./commands/doctor.js";
import { authSet, authList, authRotate, authClear, authTest } from "./commands/auth_cmd.js";
import { daemonStart, daemonStop, daemonStatus, daemonLogs, daemonInstall, daemonUninstall, startDaemonServer } from "./commands/daemon.js";
import { killswitchOn, killswitchOff, killswitchStatus } from "./commands/killswitch.js";
import { generateBashCompletion, generateFishCompletion, generatePowershellCompletion, generateZshCompletion } from "./commands/completion.js";
import { installPlugin, listPlugins, removePlugin, printPluginList } from "./plugins/manager.js";
import { addTrigger, listTriggers, setTriggerEnabled, removeTrigger, printTriggerList } from "./triggers/manager.js";
import { readAuditLog, appendAuditLog } from "./trust.js";
import { loadConfig, writeDefaultConfig } from "./config.js";
import { createMcpClient, extractText } from "./mcp_client.js";
import { checkTrustGate, getTier, isKillSwitchActive } from "./trust.js";
import { ReplApp } from "./repl/App.js";

// ── Internal daemon start (called by daemon.start spawned subprocess) ──────────
if (process.env.ALGOCHAINS_DAEMON_INTERNAL === "1") {
  startDaemonServer().catch(e => { console.error(e); process.exit(1); });
  process.exit(0); // never reached — server runs forever
}

const VERSION = "22.4.1";

// ── Root program ────────────────────────────────────────────────────────────────
const program = new Command("algochains")
  .version(VERSION)
  .description("AlgoChains CLI — AI-native algorithmic trading with 482 MCP tools")
  .option("--profile <name>", "active credential profile (demo|paper|live)", "demo")
  .option("--dry-run", "preview T2/T3 actions without executing")
  .option("--safe-only", "block all T2/T3 tools (read + compute only)")
  .option("--confirm", "required for T3/LIVE tools")
  .option("--json", "output structured JSON")
  .option("--verbose", "verbose output");

// ── Interactive REPL (no command = launch REPL) ────────────────────────────────
program.action(async (opts: Record<string, unknown>) => {
  const { render: inkRender } = await import("ink");
  await prefetchPromises;
  inkRender(React.createElement(ReplApp, { profileName: opts.profile as string | undefined }));
});

// ── doctor ─────────────────────────────────────────────────────────────────────
program
  .command("doctor")
  .description("Pre-flight health checks for all system components")
  .option("--fix", "attempt auto-fix for failed checks")
  .option("--quick", "run only critical checks")
  .option("--json", "output JSON")
  .action(async (opts) => {
    const config = loadConfig();
    const profile = opts.parent?.opts().profile;
    const results = await runDoctorCheck(profile, opts.quick, opts.fix);
    if (opts.json) {
      console.log(JSON.stringify(results, null, 2));
    } else {
      printDoctorResults(results);
    }
    const hasFail = results.some(r => r.status === "fail");
    process.exit(hasFail ? 1 : 0);
  });

// ── auth ───────────────────────────────────────────────────────────────────────
const authCmd = program.command("auth").description("Manage broker and API credentials");

authCmd.command("set <service>")
  .description("Store credentials in OS keyring (interactive prompts)")
  .action(authSet);

authCmd.command("list")
  .description("Show authenticated services")
  .action(authList);

authCmd.command("rotate <service>")
  .description("Re-enter and update credentials for a service")
  .action(authRotate);

authCmd.command("clear <service|all>")
  .description("Remove credentials for a service or all services")
  .action(authClear);

authCmd.command("test <service>")
  .description("Verify credentials are valid")
  .action(authTest);

// ── paper (subscriber portfolio) ───────────────────────────────────────────────
const paperCmd = program.command("paper").description("AlgoChains Paper subscriber portfolio (sub_live_ key)");

async function callPaperTool(
  tool: string,
  args: Record<string, unknown>,
  json?: boolean,
): Promise<never> {
  const config = loadConfig();
  const bridgeUrl =
    process.env.ALGOCHAINS_BRIDGE_URL
    ?? config.mcp?.bridge_url
    ?? "https://api.algochains.ai";
  if (!process.env.ALGOCHAINS_SUB_KEY) {
    console.error("  Set ALGOCHAINS_SUB_KEY (sub_live_… from algochains.ai → Account → API Keys)");
    process.exit(1);
  }
  const mcp = createMcpClient(bridgeUrl, config.mcp?.timeout_ms ?? 30_000);
  try {
    const result = await mcp.callTool(tool, args);
    if (json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      console.log(extractText(result));
    }
    process.exit(result.isError ? 1 : 0);
  } catch (e) {
    console.error(`Error: ${e}`);
    process.exit(1);
  }
}

paperCmd
  .command("status")
  .description("Show paper balance, assignments, and recent P&L via get_my_portfolio")
  .option("--json", "output structured JSON")
  .action((opts: { json?: boolean }) => callPaperTool("get_my_portfolio", {}, opts.json));

paperCmd
  .command("positions")
  .description("Show pending and recently-filled self-directed paper orders")
  .option("--json", "output structured JSON")
  .action((opts: { json?: boolean }) => callPaperTool("get_my_paper_positions", {}, opts.json));

paperCmd
  .command("order <side> <symbol> <qty>")
  .description("Place a self-directed paper order (filled at real quotes), e.g. paper order buy MNQ 1")
  .option("--limit <price>", "limit price (default: market order)")
  .option("--json", "output structured JSON")
  .action((side: string, symbol: string, qty: string, opts: { limit?: string; json?: boolean }) => {
    const parsedQty = parseInt(qty, 10);
    if (!Number.isFinite(parsedQty) || parsedQty <= 0) {
      console.error(`  Invalid qty: ${qty}`);
      process.exit(1);
    }
    const args: Record<string, unknown> = {
      side: side.toUpperCase(),
      symbol: symbol.toUpperCase(),
      qty: parsedQty,
    };
    if (opts.limit !== undefined) {
      const limitPrice = parseFloat(opts.limit);
      if (!Number.isFinite(limitPrice) || limitPrice <= 0) {
        console.error(`  Invalid --limit price: ${opts.limit}`);
        process.exit(1);
      }
      args.order_type = "limit";
      args.limit_price = limitPrice;
    }
    return callPaperTool("place_paper_order", args, opts.json);
  });

paperCmd
  .command("cancel <orderId>")
  .description("Cancel a pending self-directed paper order")
  .option("--json", "output structured JSON")
  .action((orderId: string, opts: { json?: boolean }) =>
    callPaperTool("cancel_paper_order", { order_id: orderId }, opts.json));

// ── daemon ─────────────────────────────────────────────────────────────────────
const daemonCmd = program.command("daemon").description("Background daemon with SSE streaming");

daemonCmd.command("start").description("Start daemon in background").action(daemonStart);
daemonCmd.command("stop").description("Stop running daemon").action(daemonStop);
daemonCmd.command("status").description("Check daemon health").action(daemonStatus);
daemonCmd.command("logs").option("-n <lines>", "number of lines", "100").description("Stream daemon logs").action((opts) => daemonLogs(parseInt(opts.n, 10)));
daemonCmd.command("install").description("Register as launchd service (macOS auto-start)").action(daemonInstall);
daemonCmd.command("uninstall").description("Remove launchd service").action(daemonUninstall);

// Internal: called by spawned daemon process
program.command("_daemon-internal-start", { hidden: true }).action(async () => {
  await startDaemonServer();
});

// ── killswitch ─────────────────────────────────────────────────────────────────
const ksCmd = program.command("killswitch").description("Emergency stop — block all T2/T3 operations");

ksCmd.command("on")
  .description("Activate kill switch")
  .option("--reason <text>", "reason for activation")
  .action((opts) => killswitchOn(opts.reason));

ksCmd.command("off")
  .description("Deactivate kill switch")
  .action(killswitchOff);

ksCmd.command("status")
  .description("Show kill switch state")
  .action(killswitchStatus);

// ── audit ──────────────────────────────────────────────────────────────────────
const auditCmd = program.command("audit").description("View audit log of T2/T3 operations");

auditCmd.command("tail")
  .description("Stream recent audit entries")
  .option("-n <count>", "number of entries", "50")
  .action((opts) => {
    const entries = Array.from(readAuditLog(parseInt(opts.n, 10)));
    for (const e of entries) {
      const sym = e.result === "success" ? "✓" : "✗";
      console.log(`${sym}  [${e.tier}]  ${e.ts.slice(0, 19)}  ${e.tool}  ${e.result}`);
    }
    if (entries.length === 0) console.log("  (no audit entries yet)");
  });

auditCmd.command("json")
  .description("Output audit log as JSON")
  .option("-n <count>", "number of entries", "100")
  .action((opts) => {
    const entries = Array.from(readAuditLog(parseInt(opts.n, 10)));
    console.log(JSON.stringify(entries, null, 2));
  });

// ── completion ─────────────────────────────────────────────────────────────────
program
  .command("completion <shell>")
  .description("Generate shell completion script (bash|zsh|fish|powershell)")
  .action((shell: string) => {
    const shells: Record<string, () => string> = {
      bash: generateBashCompletion,
      zsh: generateZshCompletion,
      fish: generateFishCompletion,
      powershell: generatePowershellCompletion,
      ps1: generatePowershellCompletion,
    };
    const gen = shells[shell.toLowerCase()];
    if (!gen) {
      console.error(`Unknown shell: ${shell}. Supported: bash, zsh, fish, powershell`);
      process.exit(1);
    }
    process.stdout.write(gen());
  });

// ── plugin ─────────────────────────────────────────────────────────────────────
const pluginCmd = program.command("plugin").description("Manage CLI plugins");

pluginCmd.command("install <name>")
  .description("Install a plugin (official or github:user/repo)")
  .option("--allow-community", "allow community (unsigned) plugins")
  .action((name, opts) => installPlugin(name, opts.allowCommunity));

pluginCmd.command("list")
  .description("List installed plugins")
  .action(printPluginList);

pluginCmd.command("remove <name>")
  .description("Remove an installed plugin")
  .action(removePlugin);

pluginCmd.command("update <name>")
  .description("Update an installed plugin to latest")
  .action(async (name) => {
    await installPlugin(name, false);
    console.log(`  ✓ ${name} updated`);
  });

// ── trigger ────────────────────────────────────────────────────────────────────
const triggerCmd = program.command("trigger").description("Automation triggers (cron, watch, webhook, datetime)");

triggerCmd.command("add")
  .description("Add a trigger")
  .argument("<type>", "cron|watch|webhook|datetime")
  .argument("<schedule-or-path>", "cron expression, file path, webhook path, or ISO datetime")
  .argument("<command>", "CLI command to run")
  .action((type, schedOrPath, command) => {
    const opts: Record<string, string> = {};
    if (type === "cron")     opts.schedule = schedOrPath;
    if (type === "watch")    opts.path = schedOrPath;
    if (type === "webhook")  opts.endpoint = schedOrPath;
    if (type === "datetime") opts.datetime = schedOrPath;
    const t = addTrigger(type as Parameters<typeof addTrigger>[0], command, opts);
    console.log(`  ✓ Trigger added [${t.id}]: ${type} → ${command}`);
    console.log(`  Activate by starting the daemon: algochains daemon start`);
  });

triggerCmd.command("list")
  .description("List configured triggers")
  .action(printTriggerList);

triggerCmd.command("disable <id>")
  .description("Disable a trigger by ID")
  .action((id) => { setTriggerEnabled(id, false); console.log(`  ✓ Trigger ${id} disabled`); });

triggerCmd.command("enable <id>")
  .description("Enable a trigger by ID")
  .action((id) => { setTriggerEnabled(id, true); console.log(`  ✓ Trigger ${id} enabled`); });

triggerCmd.command("remove <id>")
  .description("Remove a trigger by ID")
  .action((id) => { removeTrigger(id); console.log(`  ✓ Trigger ${id} removed`); });

// ── config ─────────────────────────────────────────────────────────────────────
const configCmd = program.command("config").description("CLI configuration management");

configCmd.command("init")
  .description("Initialize ~/.algochains/config.toml with defaults")
  .action(() => {
    writeDefaultConfig();
    console.log(`  ✓ Config written to ${CONFIG_DIR}/config.toml`);
  });

configCmd.command("show")
  .description("Show current configuration")
  .action(() => {
    const cfg = loadConfig();
    console.log(JSON.stringify(cfg, null, 2));
  });

configCmd.command("generate <target>")
  .description("Generate IDE MCP config (cursor|claude-desktop|windsurf)")
  .action(async (target: string) => {
    // Delegate to quickstart.py for IDE config generation
    const { execSync } = await import("child_process");
    try {
      execSync(`python3 scripts/quickstart.py --generate-config ${target}`, { stdio: "inherit", cwd: process.env.ALGOCHAINS_MCP_SERVER_ROOT });
    } catch {
      console.error(`  Failed to generate ${target} config. Is algochains-mcp-server installed?`);
      process.exit(1);
    }
  });

// ── Direct tool pass-through ───────────────────────────────────────────────────
// Any unrecognized command is treated as an MCP tool call
program.on("command:*", async ([toolName, ...rest]: string[]) => {
  const config = loadConfig();
  const opts = program.opts<{ profile: string; dryRun: boolean; safeOnly: boolean; confirm: boolean; json: boolean }>();
  const profile = opts.profile ?? "demo";
  const cfgProfile = config.profile[profile] ?? config.profile.demo;
  const mcp = createMcpClient(cfgProfile.mcp_bridge_url ?? config.mcp.bridge_url, config.mcp.timeout_ms);

  const gateResult = checkTrustGate({
    command: toolName,
    profile: cfgProfile.mode,
    dryRun: opts.dryRun,
    safeOnly: opts.safeOnly,
    confirm: opts.confirm,
  });

  if (!gateResult.allowed) {
    console.error(`\n  🛑 ${gateResult.reason}`);
    console.error(`     ${gateResult.hint}\n`);
    process.exit(1);
  }

  // Parse remaining args as --key value pairs or JSON
  let args: Record<string, unknown> = {};
  if (rest.length === 1 && rest[0].startsWith("{")) {
    try { args = JSON.parse(rest[0]); } catch { args = { query: rest[0] }; }
  } else {
    for (let i = 0; i < rest.length; i++) {
      if (rest[i].startsWith("--")) {
        const key = rest[i].slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        const val = rest[i + 1] ?? "true";
        args[key] = val === "true" ? true : val === "false" ? false : isNaN(+val) ? val : +val;
        i++;
      }
    }
  }

  const start = Date.now();
  const toolNameSnake = toolName.replace(/-/g, "_");

  if (opts.dryRun) {
    console.log(`⏸️  DRY-RUN: would call ${toolNameSnake} with args:`);
    console.log(JSON.stringify(args, null, 2));
    process.exit(0);
  }

  try {
    const result = await mcp.callTool(toolNameSnake, args);
    const ms = Date.now() - start;

    if (opts.json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      console.log(extractText(result));
    }

    // Audit T2/T3
    const tier = getTier(toolName);
    if (tier === "T2" || tier === "T3") {
      appendAuditLog({ ts: new Date().toISOString(), tier, tool: toolNameSnake, args, profile, result: result.isError ? "error" : "success", duration_ms: ms });
    }

    process.exit(result.isError ? 1 : 0);
  } catch (e) {
    console.error(`Error: ${e}`);
    process.exit(1);
  }
});

// ── Parse ──────────────────────────────────────────────────────────────────────
program.parse();
