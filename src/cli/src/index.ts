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
import {
  accountSignup, accountLogin, accountLogout, accountStatus,
  mfaEnroll, mfaVerify, mfaList, mfaRemove,
} from "./commands/account_cmd.js";
import { keysCreate, keysList, keysRotate, keysRevoke, keysTest } from "./commands/keys_cmd.js";
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

const VERSION = "22.5.0";

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
    ?? "https://mcp.algochains.ai";
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

// ── subscribe ─────────────────────────────────────────────────────────────────
program
  .command("subscribe")
  .description("Get a Stripe checkout URL for an AlgoChains subscription tier")
  .requiredOption("--tier <tier>", "subscription tier: paper|live")
  .requiredOption("--email <email>", "email address for key delivery")
  .option("--json", "output structured JSON")
  .action(async (opts: { tier: string; email: string; json?: boolean }) => {
    const config = loadConfig();
    const bridgeUrl =
      process.env.ALGOCHAINS_BRIDGE_URL
      ?? config.mcp?.bridge_url
      ?? "https://mcp.algochains.ai";
    const mcp = createMcpClient(bridgeUrl, config.mcp?.timeout_ms ?? 30_000);

    let checkoutUrl: string | undefined;
    try {
      const result = await mcp.callTool("get_checkout_url", { tier: opts.tier, email: opts.email });
      if (result.isError) {
        console.error(`  Error: ${extractText(result)}`);
        process.exit(1);
      }
      if (opts.json) {
        console.log(JSON.stringify(result, null, 2));
        process.exit(0);
      }
      const text = extractText(result);
      // Extract URL from tool response — expect a bare URL or JSON with a url field
      const urlMatch = text.match(/https:\/\/checkout\.stripe\.com\/[^\s"')]+/);
      checkoutUrl = urlMatch ? urlMatch[0] : text.trim();
    } catch (e) {
      console.error(`  Error contacting MCP server: ${e}`);
      process.exit(1);
    }

    console.log(`  ✓ Checkout URL: ${checkoutUrl}`);
    console.log(`  → Opening in browser...`);

    // Open URL in the default browser cross-platform
    const { execSync } = await import("child_process");
    try {
      if (process.platform === "darwin") {
        execSync(`open "${checkoutUrl}"`, { stdio: "ignore" });
      } else if (process.platform === "win32") {
        // start requires an empty title string before the URL on Windows
        execSync(`start "" "${checkoutUrl}"`, { stdio: "ignore", shell: true });
      } else {
        execSync(`xdg-open "${checkoutUrl}"`, { stdio: "ignore" });
      }
    } catch {
      console.log(`  (Could not auto-open browser — paste the URL above manually)`);
    }

    console.log(`  → After payment, your sub_live_… key will be emailed to ${opts.email}`);
    console.log(`  → Then run: export ALGOCHAINS_SUBSCRIBER_KEY=<your-key>`);
    console.log(`  → Verify:   algochains subscriber-status`);
    process.exit(0);
  });

// ── join-bot ───────────────────────────────────────────────────────────────────
program
  .command("join-bot <bot>")
  .description("Subscribe to copy-trade signals from a live bot (e.g. mnq, cl, mes, nq)")
  .option("--size <fraction>", "position-size multiplier (0.0–1.0), default 1.0")
  .option("--max-contracts <n>", "maximum contracts per signal")
  .option("--json", "output structured JSON")
  .action(async (bot: string, opts: { size?: string; maxContracts?: string; json?: boolean }) => {
    const subscriberKey = process.env.ALGOCHAINS_SUBSCRIBER_KEY;
    if (!subscriberKey) {
      console.error("  Set ALGOCHAINS_SUBSCRIBER_KEY first. Run: algochains subscribe");
      process.exit(1);
    }

    const config = loadConfig();
    const bridgeUrl =
      process.env.ALGOCHAINS_BRIDGE_URL
      ?? config.mcp?.bridge_url
      ?? "https://mcp.algochains.ai";
    const mcp = createMcpClient(bridgeUrl, config.mcp?.timeout_ms ?? 30_000);

    const args: Record<string, unknown> = { bot: bot.toLowerCase() };
    if (opts.size !== undefined) {
      const size = parseFloat(opts.size);
      if (!Number.isFinite(size) || size <= 0 || size > 1) {
        console.error(`  Invalid --size: ${opts.size}. Must be between 0.0 and 1.0.`);
        process.exit(1);
      }
      args.size_multiplier = size;
    }
    if (opts.maxContracts !== undefined) {
      const mc = parseInt(opts.maxContracts, 10);
      if (!Number.isFinite(mc) || mc <= 0) {
        console.error(`  Invalid --max-contracts: ${opts.maxContracts}. Must be a positive integer.`);
        process.exit(1);
      }
      args.max_contracts = mc;
    }

    try {
      const result = await mcp.callTool("join_bot", args);
      if (opts.json) {
        console.log(JSON.stringify(result, null, 2));
        process.exit(result.isError ? 1 : 0);
      }
      if (result.isError) {
        const msg = extractText(result);
        // Surface capacity error with a helpful hint
        if (/capacity|full|max.*subscriber/i.test(msg)) {
          const botUpper = bot.toUpperCase();
          console.error(`  ${botUpper} bot is at capacity (20 subscribers). Try CL, MES, or NQ.`);
        } else {
          console.error(`  Error: ${msg}`);
        }
        process.exit(1);
      }
      const botDisplay = bot.toUpperCase();
      // Try to extract the canonical bot name from the response if present
      const text = extractText(result);
      const nameMatch = text.match(/[A-Z]{2,}_[A-Za-z_]+/);
      const displayName = nameMatch ? nameMatch[0] : `${botDisplay}_Scalper`;
      console.log(`  ✓ Subscribed to ${displayName}`);
      console.log(`  → Copy-trade signals active immediately`);
      console.log(`  → Run: algochains signal-stream  (to see live signals)`);
      process.exit(0);
    } catch (e) {
      console.error(`  Error: ${e}`);
      process.exit(1);
    }
  });

// ── subscriber-status ──────────────────────────────────────────────────────────
program
  .command("subscriber-status")
  .description("Show subscriber key info, tier, bot assignments, paper balance, and fills today")
  .option("--json", "output structured JSON")
  .action(async (opts: { json?: boolean }) => {
    const subscriberKey = process.env.ALGOCHAINS_SUBSCRIBER_KEY;
    if (!subscriberKey) {
      console.error("  Set ALGOCHAINS_SUBSCRIBER_KEY first. Run: algochains subscribe");
      process.exit(1);
    }

    const config = loadConfig();
    const bridgeUrl =
      process.env.ALGOCHAINS_BRIDGE_URL
      ?? config.mcp?.bridge_url
      ?? "https://mcp.algochains.ai";
    const mcp = createMcpClient(bridgeUrl, config.mcp?.timeout_ms ?? 30_000);

    try {
      const result = await mcp.callTool("get_subscriber_status", {});
      if (opts.json) {
        console.log(JSON.stringify(result, null, 2));
        process.exit(result.isError ? 1 : 0);
      }
      if (result.isError) {
        console.error(`  Error: ${extractText(result)}`);
        process.exit(1);
      }
      // Pretty-print key fields if we got structured data; otherwise dump text
      const text = extractText(result);
      let parsed: Record<string, unknown> | null = null;
      try { parsed = JSON.parse(text); } catch { /* not JSON */ }

      if (parsed) {
        const keyPrefix = String(parsed.key ?? parsed.subscriber_key ?? subscriberKey).slice(0, 16) + "…";
        console.log(`  Key prefix    : ${keyPrefix}`);
        if (parsed.tier)           console.log(`  Tier          : ${parsed.tier}`);
        if (parsed.bots_assigned)  console.log(`  Bots assigned : ${parsed.bots_assigned}`);
        if (parsed.paper_balance !== undefined) console.log(`  Paper balance : $${parsed.paper_balance}`);
        if (parsed.fills_today !== undefined)   console.log(`  Fills today   : ${parsed.fills_today}`);
      } else {
        console.log(text);
      }
      process.exit(0);
    } catch (e) {
      console.error(`  Error: ${e}`);
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

// ── account ────────────────────────────────────────────────────────────────────
const accountCmd = program
  .command("account")
  .description("AlgoChains platform account management (signup, login, logout, status)");

accountCmd
  .command("signup")
  .description("Create a new AlgoChains account")
  .option("--email <email>", "Email address")
  .option("--no-mfa", "Skip MFA enrollment guidance")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    await accountSignup({ email: opts.email, noMfa: opts.noMfa, json: opts.json });
  });

accountCmd
  .command("login")
  .description("Login to AlgoChains")
  .option("--email <email>", "Email address")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    await accountLogin({ email: opts.email, json: opts.json });
  });

accountCmd
  .command("logout")
  .description("Logout from AlgoChains")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    await accountLogout({ json: opts.json });
  });

accountCmd
  .command("status")
  .description("Show current onboarding status, MFA factors, and session info")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    await accountStatus({ json: opts.json });
  });

// ── auth mfa subcommands ────────────────────────────────────────────────────────
// Extend the existing 'auth' command with an 'mfa' subcommand group

const authCmd = program.commands.find(c => c.name() === "auth");
if (authCmd) {
  const mfaCmd = authCmd
    .command("mfa")
    .description("Multi-factor authentication (TOTP/SMS enrollment and management)");

  mfaCmd
    .command("enroll")
    .description("Enroll a new MFA factor (TOTP authenticator or SMS)")
    .option("--type <type>", "Factor type: totp or phone", "totp")
    .option("--json", "Output JSON")
    .action(async (opts) => {
      await mfaEnroll({ type: opts.type, json: opts.json });
    });

  mfaCmd
    .command("verify <code>")
    .description("Verify MFA code to complete enrollment or step up to AAL2")
    .requiredOption("--factor-id <id>", "Factor ID from 'algochains auth mfa list'")
    .option("--challenge-id <id>", "Challenge ID (for login step-up)")
    .option("--json", "Output JSON")
    .action(async (code, opts) => {
      await mfaVerify(code, { factorId: opts.factorId, challengeId: opts.challengeId, json: opts.json });
    });

  mfaCmd
    .command("list")
    .description("List enrolled MFA factors")
    .option("--json", "Output JSON")
    .action(async (opts) => {
      await mfaList({ json: opts.json });
    });

  mfaCmd
    .command("remove <factor-id>")
    .description("Remove an enrolled MFA factor (requires OWNER_API_TOKEN)")
    .requiredOption("--owner-token <token>", "Owner API token")
    .option("--json", "Output JSON")
    .action(async (factorId, opts) => {
      await mfaRemove(factorId, opts.ownerToken, { json: opts.json });
    });
}

// ── keys ────────────────────────────────────────────────────────────────────────
const keysCmd = program
  .command("keys")
  .description("Developer API key lifecycle (create, list, rotate, revoke, test)");

keysCmd
  .command("create")
  .description("Create a new developer API key (requires AAL2 session)")
  .option("--name <name>", "Friendly name for the key", "default")
  .option("--scopes <scopes...>", "Scopes e.g. read:market_data read:signals")
  .option("--env <env>", "Key environment: live or test", "live")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    await keysCreate({ name: opts.name, scopes: opts.scopes, env: opts.env, json: opts.json });
  });

keysCmd
  .command("list")
  .description("List developer API keys (masked)")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    await keysList({ json: opts.json });
  });

keysCmd
  .command("rotate <key-id>")
  .description("Rotate a developer API key (revoke old, mint new; requires AAL2)")
  .option("--name <name>", "Name for the new key")
  .option("--json", "Output JSON")
  .action(async (keyId, opts) => {
    await keysRotate(keyId, { name: opts.name, json: opts.json });
  });

keysCmd
  .command("revoke <key-id>")
  .description("Revoke a developer API key (requires AAL2)")
  .option("--json", "Output JSON")
  .action(async (keyId, opts) => {
    await keysRevoke(keyId, { json: opts.json });
  });

keysCmd
  .command("test")
  .description("Test a developer API key against the AlgoChains bridge")
  .option("--key <key>", "Key to test (defaults to AC_DEV_KEY env var)")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    await keysTest({ key: opts.key, json: opts.json });
  });

// ── Parse ──────────────────────────────────────────────────────────────────────
program.parse();
