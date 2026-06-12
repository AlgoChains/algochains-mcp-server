/**
 * algochains daemon — Background process with SSE streaming
 *
 * Commands:
 *   algochains daemon start    — start background daemon
 *   algochains daemon stop     — graceful shutdown
 *   algochains daemon status   — health check
 *   algochains daemon logs     — stream daemon.log
 *   algochains daemon install  — register as launchd service (macOS)
 *   algochains daemon uninstall — remove launchd service
 *
 * Architecture:
 *   - Hono HTTP server on localhost:39337 (configurable)
 *   - SSE stream at /api/stream (authenticated with random token)
 *   - /health endpoint (public)
 *   - Polls bot health, regime, position changes
 *   - Desktop notifications on significant events
 *   - Token written to ~/.algochains/daemon.token (mode 0600)
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  appendFileSync,
} from "fs";
import { homedir, platform } from "os";
import { join } from "path";
import {
  DAEMON_LOG_FILE,
  DAEMON_PID_FILE,
  DAEMON_PORT_FILE,
  DAEMON_TOKEN_FILE,
  loadConfig,
} from "../config.js";
import { createMcpClient } from "../mcp_client.js";

const LAUNCHD_PLIST_PATH = join(homedir(), "Library", "LaunchAgents", "com.algochains.cli-daemon.plist");

export function isAuthorizedDaemonRequest(
  authorizationHeader: string | undefined,
  developerKeyHeader: string | undefined,
  daemonToken: string,
  bridgeKey = process.env.ALGOCHAINS_BRIDGE_KEY,
): boolean {
  const candidates = new Set<string>();
  const auth = authorizationHeader?.trim();
  if (auth) {
    candidates.add(auth);
    const bearer = auth.match(/^Bearer\s+(.+)$/i);
    if (bearer) candidates.add(bearer[1].trim());
  }
  if (developerKeyHeader?.trim()) candidates.add(developerKeyHeader.trim());

  if (daemonToken && candidates.has(daemonToken)) return true;
  return Boolean(bridgeKey && candidates.has(bridgeKey));
}

function randomToken(bytes = 32): string {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return Array.from(arr, b => b.toString(16).padStart(2, "0")).join("");
}

function log(msg: string): void {
  const line = `${new Date().toISOString()} ${msg}\n`;
  appendFileSync(DAEMON_LOG_FILE, line, { mode: 0o600 });
  process.stdout.write(line);
}

// ── Start daemon (in-process — call from forked child) ────────────────────────
export async function startDaemonServer(): Promise<void> {
  const { Hono } = await import("hono");
  const { serve } = await import("@hono/node-server").catch(async () => {
    // fallback: use built-in http
    return import("hono/node-server" as string).catch(() => {
      throw new Error("Install @hono/node-server: npm i @hono/node-server");
    });
  });

  const config = loadConfig();
  const port = config.daemon.port;
  const token = randomToken();
  const mcp = createMcpClient(config.mcp.bridge_url, 5_000);

  writeFileSync(DAEMON_PID_FILE, String(process.pid), { mode: 0o600 });
  writeFileSync(DAEMON_PORT_FILE, String(port), { mode: 0o600 });
  writeFileSync(DAEMON_TOKEN_FILE, token, { mode: 0o600 });

  log(`AlgoChains daemon starting on port ${port} (pid ${process.pid})`);

  // Event bus for SSE clients
  const clients = new Set<{ send: (data: string) => void }>();
  function broadcast(type: string, data: unknown): void {
    const msg = `data: ${JSON.stringify({ type, data, ts: Date.now() })}\n\n`;
    for (const c of clients) { try { c.send(msg); } catch { clients.delete(c); } }
  }

  const app = new Hono();

  // Public health endpoint
  app.get("/health", c => c.json({
    status: "ok",
    pid: process.pid,
    port,
    uptime_s: Math.floor(process.uptime()),
    clients: clients.size,
    version: "22.4.0",
  }));

  // Authenticated SSE stream
  app.get("/api/stream", async c => {
    const auth = c.req.header("Authorization") ?? c.req.query("token") ?? "";
    if (!auth.endsWith(token)) {
      c.status(401);
      return c.text("Unauthorized — use daemon token");
    }

    c.header("Content-Type", "text/event-stream");
    c.header("Cache-Control", "no-cache");
    c.header("Connection", "keep-alive");
    c.header("X-Accel-Buffering", "no");

    return c.body(new ReadableStream({
      start(controller) {
        const send = (data: string) => controller.enqueue(new TextEncoder().encode(data));
        clients.add({ send });
        send("data: {\"type\":\"connected\"}\n\n");

        const cleanup = () => { clients.delete({ send }); };
        c.req.raw.signal.addEventListener("abort", cleanup);
      },
    }));
  });

  // Tool proxy (authenticated)
  app.post("/tool", async c => {
    const auth = c.req.header("Authorization") ?? "";
    const developerKey = c.req.header("X-Developer-Key") ?? "";
    if (!isAuthorizedDaemonRequest(auth, developerKey, token)) {
      c.status(401); return c.text("Unauthorized");
    }
    const { tool, arguments: args } = await c.req.json<{ tool: string; arguments: Record<string, unknown> }>();
    const result = await mcp.callTool(tool, args);
    return c.json(result);
  });

  // ── Polling loops ──────────────────────────────────────────────────────────
  async function pollBotHealth(): Promise<void> {
    try {
      const res = await mcp.callTool("get_bot_health", {});
      broadcast("bot_health", res);
    } catch { /* silent */ }
  }

  async function pollRegime(): Promise<void> {
    try {
      const res = await mcp.callTool("detect_market_regime", {});
      broadcast("regime", res);
    } catch { /* silent */ }
  }

  // Start polls
  const botHealthInterval = setInterval(pollBotHealth, 30_000);
  const regimeInterval = setInterval(pollRegime, config.repl.regime_refresh_interval_ms);

  // Initial polls
  setTimeout(pollBotHealth, 2_000);
  setTimeout(pollRegime, 5_000);

  // Start server
  serve({ fetch: app.fetch, port }, (info) => {
    log(`Daemon listening on http://localhost:${info.port}`);
    log(`SSE stream: http://localhost:${info.port}/api/stream`);
  });

  // Graceful shutdown
  for (const sig of ["SIGTERM", "SIGINT"]) {
    process.on(sig, () => {
      log(`Daemon shutting down (${sig})`);
      clearInterval(botHealthInterval);
      clearInterval(regimeInterval);
      for (const f of [DAEMON_PID_FILE, DAEMON_PORT_FILE, DAEMON_TOKEN_FILE]) {
        try { require("fs").unlinkSync(f); } catch { /* ignore */ }
      }
      process.exit(0);
    });
  }
}

// ── CLI subcommands ────────────────────────────────────────────────────────────
export async function daemonStart(): Promise<void> {
  if (existsSync(DAEMON_PID_FILE)) {
    const pid = readFileSync(DAEMON_PID_FILE, "utf-8").trim();
    try {
      process.kill(parseInt(pid, 10), 0); // check if alive
      console.log(`  Daemon already running (pid ${pid})`);
      return;
    } catch { /* stale pid, start fresh */ }
  }

  const { spawn } = await import("child_process");
  const child = spawn(process.execPath, [process.argv[1], "_daemon-internal-start"], {
    detached: true,
    stdio: ["ignore", "ignore", "ignore"],
    env: { ...process.env, ALGOCHAINS_DAEMON_INTERNAL: "1" },
  });
  child.unref();

  // Wait briefly for pid file
  await new Promise(r => setTimeout(r, 500));
  if (existsSync(DAEMON_PID_FILE)) {
    const pid = readFileSync(DAEMON_PID_FILE, "utf-8").trim();
    console.log(`  ✓ Daemon started (pid ${pid})`);
    console.log(`  SSE stream: http://localhost:${loadConfig().daemon.port}/api/stream`);
  } else {
    console.error("  ✗ Daemon failed to start — check logs: algochains daemon logs");
  }
}

export async function daemonStop(): Promise<void> {
  if (!existsSync(DAEMON_PID_FILE)) {
    console.log("  Daemon is not running");
    return;
  }
  const pid = parseInt(readFileSync(DAEMON_PID_FILE, "utf-8").trim(), 10);
  try {
    process.kill(pid, "SIGTERM");
    console.log(`  ✓ Daemon stopped (pid ${pid})`);
  } catch {
    console.log(`  Daemon was not running (stale pid ${pid})`);
    try { require("fs").unlinkSync(DAEMON_PID_FILE); } catch { /* ignore */ }
  }
}

export async function daemonStatus(): Promise<void> {
  const config = loadConfig();
  const port = existsSync(DAEMON_PORT_FILE) ? parseInt(readFileSync(DAEMON_PORT_FILE, "utf-8").trim(), 10) : config.daemon.port;

  try {
    const res = await fetch(`http://localhost:${port}/health`, { signal: AbortSignal.timeout(2_000) });
    const data = await res.json() as Record<string, unknown>;
    console.log("  ✓ Daemon running:");
    console.log(`     pid:      ${data.pid}`);
    console.log(`     port:     ${data.port}`);
    console.log(`     uptime:   ${data.uptime_s}s`);
    console.log(`     clients:  ${data.clients} SSE connections`);
  } catch {
    if (existsSync(DAEMON_PID_FILE)) {
      console.log("  ✗ Daemon pid file exists but HTTP is unreachable");
    } else {
      console.log("  – Daemon is not running");
      console.log("    Start with: algochains daemon start");
    }
  }
}

export async function daemonLogs(lines = 100): Promise<void> {
  if (!existsSync(DAEMON_LOG_FILE)) {
    console.log("  No daemon log found");
    return;
  }
  const content = readFileSync(DAEMON_LOG_FILE, "utf-8").trim();
  const recent = content.split("\n").slice(-lines).join("\n");
  console.log(recent);
}

export async function daemonInstall(): Promise<void> {
  if (platform() !== "darwin") {
    console.error("  launchd installation is only supported on macOS");
    process.exit(1);
  }

  const cliPath = process.argv[1];
  const plist = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.algochains.cli-daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>${process.execPath}</string>
    <string>${cliPath}</string>
    <string>daemon</string>
    <string>start</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>${DAEMON_LOG_FILE}</string>
  <key>StandardErrorPath</key>
  <string>${DAEMON_LOG_FILE}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ALGOCHAINS_DAEMON_INTERNAL</key>
    <string>1</string>
  </dict>
</dict>
</plist>`;

  writeFileSync(LAUNCHD_PLIST_PATH, plist);
  const { execSync } = await import("child_process");
  execSync(`launchctl load ${LAUNCHD_PLIST_PATH}`);
  console.log(`  ✓ Daemon registered as launchd service`);
  console.log(`     Plist: ${LAUNCHD_PLIST_PATH}`);
  console.log(`     Will start automatically on login`);
}

export async function daemonUninstall(): Promise<void> {
  if (!existsSync(LAUNCHD_PLIST_PATH)) {
    console.log("  Daemon launchd service not installed");
    return;
  }
  const { execSync } = await import("child_process");
  execSync(`launchctl unload ${LAUNCHD_PLIST_PATH}`);
  require("fs").unlinkSync(LAUNCHD_PLIST_PATH);
  console.log("  ✓ Daemon launchd service removed");
}
