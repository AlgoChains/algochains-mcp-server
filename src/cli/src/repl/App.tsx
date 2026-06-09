/**
 * AlgoChains CLI — Interactive REPL
 * Built with React + Ink (React for terminal)
 *
 * Features:
 *   - Streaming tool output with spinners
 *   - Slash commands: /help /tools /cost /history /profile /kill /doctor /exit
 *   - Tab autocomplete for tool names
 *   - Regime header bar (updates every 5 min)
 *   - Session history to ~/.algochains/history.jsonl
 *   - Kill switch indicator
 *   - Session cost tracking
 *   - Daemon SSE connection when available
 */
import React, { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Box, Text, useApp, useInput, useStdin } from "ink";
import { createMcpClient, extractText, McpToolResult } from "../mcp_client.js";
import { appendAuditLog, checkTrustGate, getTier } from "../trust.js";
import { HISTORY_FILE, getActiveProfile, loadConfig } from "../config.js";
import { isKillSwitchActive } from "../trust.js";
import { appendFileSync, existsSync, readFileSync } from "fs";

// ── Types ─────────────────────────────────────────────────────────────────────
interface HistoryEntry {
  command: string;
  result: string;
  ts: number;
  duration_ms: number;
  error: boolean;
}

interface ReplState {
  input: string;
  cursor: number;
  output: Array<{ text: string; type: "user" | "result" | "error" | "info" | "system" }>;
  history: string[];        // command history (for up-arrow)
  historyIdx: number;
  loading: boolean;
  loadingTool: string;
  regime: string;
  killSwitch: boolean;
  sessionCost: number;      // estimated API calls * avg cost
  sessionCalls: number;
  profileName: string;
}

type ReplAction =
  | { type: "SET_INPUT"; input: string; cursor: number }
  | { type: "ADD_OUTPUT"; text: string; kind: HistoryEntry["error"] extends boolean ? "user" | "result" | "error" | "info" | "system" : never }
  | { type: "SET_LOADING"; loading: boolean; tool?: string }
  | { type: "SET_REGIME"; regime: string }
  | { type: "SET_KILL_SWITCH"; active: boolean }
  | { type: "INCR_COST" }
  | { type: "NAV_HISTORY"; dir: "up" | "down" }
  | { type: "CLEAR" };

function replReducer(state: ReplState, action: ReplAction): ReplState {
  switch (action.type) {
    case "SET_INPUT": return { ...state, input: action.input, cursor: action.cursor };
    case "ADD_OUTPUT": return {
      ...state,
      output: [...state.output.slice(-200), { text: action.text, type: (action as { kind: string }).kind ?? "result" }],
    };
    case "SET_LOADING": return { ...state, loading: action.loading, loadingTool: action.tool ?? state.loadingTool };
    case "SET_REGIME": return { ...state, regime: action.regime };
    case "SET_KILL_SWITCH": return { ...state, killSwitch: action.active };
    case "INCR_COST": return { ...state, sessionCalls: state.sessionCalls + 1, sessionCost: state.sessionCost + 0.002 };
    case "NAV_HISTORY": {
      const idx = action.dir === "up"
        ? Math.min(state.historyIdx + 1, state.history.length)
        : Math.max(state.historyIdx - 1, 0);
      const input = state.history[state.history.length - idx] ?? "";
      return { ...state, historyIdx: idx, input, cursor: input.length };
    }
    case "CLEAR": return { ...state, output: [] };
    default: return state;
  }
}

// ── REPL App ──────────────────────────────────────────────────────────────────
export function ReplApp({ profileName }: { profileName?: string }) {
  const { exit } = useApp();
  const config = loadConfig();
  const profile = getActiveProfile(profileName, config);
  const mcp = createMcpClient(profile.mcp_bridge_url ?? config.mcp.bridge_url, config.mcp.timeout_ms);

  const [state, dispatch] = useReducer(replReducer, {
    input: "",
    cursor: 0,
    output: [{
      text: `AlgoChains v22.4.1  |  Profile: ${profileName ?? config.default.profile}  |  Type /help for commands`,
      type: "system",
    }],
    history: loadCommandHistory(),
    historyIdx: 0,
    loading: false,
    loadingTool: "",
    regime: "detecting...",
    killSwitch: isKillSwitchActive(),
    sessionCost: 0,
    sessionCalls: 0,
    profileName: profileName ?? config.default.profile,
  });

  // ── Regime polling ──────────────────────────────────────────────────────────
  useEffect(() => {
    let mounted = true;
    async function fetchRegime() {
      try {
        const res = await mcp.callTool("detect_market_regime", {});
        if (!mounted) return;
        const text = extractText(res);
        const match = text.match(/regime[:\s]+([A-Za-z_]+)/i);
        dispatch({ type: "SET_REGIME", regime: match?.[1] ?? text.slice(0, 20) });
      } catch { /* ignore */ }
    }
    fetchRegime();
    const iv = setInterval(fetchRegime, config.repl.regime_refresh_interval_ms);
    return () => { mounted = false; clearInterval(iv); };
  }, []);

  // ── Kill switch polling ─────────────────────────────────────────────────────
  useEffect(() => {
    const iv = setInterval(() => {
      dispatch({ type: "SET_KILL_SWITCH", active: isKillSwitchActive() });
    }, 5_000);
    return () => clearInterval(iv);
  }, []);

  // ── Command execution ───────────────────────────────────────────────────────
  const executeCommand = useCallback(async (raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return;

    dispatch({ type: "ADD_OUTPUT", text: `> ${trimmed}`, kind: "user" } as Parameters<typeof dispatch>[0]);
    saveCommandHistory(trimmed, state.history);

    // Slash commands
    if (trimmed.startsWith("/")) {
      handleSlashCommand(trimmed, { dispatch, exit, mcp, config, profile });
      return;
    }

    // Parse tool name and JSON args
    const [toolName, ...argParts] = trimmed.split(" ");
    let args: Record<string, unknown> = {};
    if (argParts.length) {
      const argStr = argParts.join(" ");
      if (argStr.startsWith("{")) {
        try { args = JSON.parse(argStr); } catch { args = { query: argStr }; }
      } else {
        args = parseKeyValueArgs(argParts);
      }
    }

    // Trust gate
    const gateResult = checkTrustGate({
      command: toolName,
      profile: profile.mode,
      dryRun: false,
      safeOnly: false,
    });

    if (!gateResult.allowed) {
      dispatch({ type: "ADD_OUTPUT", text: `🛑 ${gateResult.reason}\n   ${gateResult.hint}`, kind: "error" } as Parameters<typeof dispatch>[0]);
      return;
    }

    // Execute
    dispatch({ type: "SET_LOADING", loading: true, tool: toolName });
    const start = Date.now();
    try {
      const result = await mcp.callTool(toolName.replace(/-/g, "_"), args);
      const ms = Date.now() - start;
      const text = extractText(result);
      dispatch({ type: "ADD_OUTPUT", text: result.isError ? `✗ ${text}` : text, kind: result.isError ? "error" : "result" } as Parameters<typeof dispatch>[0]);
      dispatch({ type: "INCR_COST" });

      // Audit log for T2/T3
      const tier = getTier(toolName);
      if (tier === "T2" || tier === "T3") {
        appendAuditLog({
          ts: new Date().toISOString(),
          tier,
          tool: toolName,
          args,
          profile: profileName,
          result: result.isError ? "error" : "success",
          duration_ms: ms,
        });
      }
    } catch (e) {
      dispatch({ type: "ADD_OUTPUT", text: `Error: ${String(e)}`, kind: "error" } as Parameters<typeof dispatch>[0]);
    } finally {
      dispatch({ type: "SET_LOADING", loading: false });
    }
  }, [state.history, profile]);

  // ── Key handling ────────────────────────────────────────────────────────────
  useInput((input, key) => {
    if (state.loading) return;

    if (key.return) {
      executeCommand(state.input);
      dispatch({ type: "SET_INPUT", input: "", cursor: 0 });
      return;
    }
    if (key.backspace || key.delete) {
      const next = state.input.slice(0, state.cursor - 1) + state.input.slice(state.cursor);
      dispatch({ type: "SET_INPUT", input: next, cursor: Math.max(0, state.cursor - 1) });
      return;
    }
    if (key.upArrow) { dispatch({ type: "NAV_HISTORY", dir: "up" }); return; }
    if (key.downArrow) { dispatch({ type: "NAV_HISTORY", dir: "down" }); return; }
    if (key.leftArrow) { dispatch({ type: "SET_INPUT", input: state.input, cursor: Math.max(0, state.cursor - 1) }); return; }
    if (key.rightArrow) { dispatch({ type: "SET_INPUT", input: state.input, cursor: Math.min(state.input.length, state.cursor + 1) }); return; }
    if (key.ctrl && input === "c") { exit(); return; }
    if (key.ctrl && input === "l") { dispatch({ type: "CLEAR" }); return; }

    if (!key.ctrl && !key.meta) {
      const next = state.input.slice(0, state.cursor) + input + state.input.slice(state.cursor);
      dispatch({ type: "SET_INPUT", input: next, cursor: state.cursor + input.length });
    }
  });

  // ── Render ──────────────────────────────────────────────────────────────────
  const killColor = state.killSwitch ? "red" : "green";
  const killLabel = state.killSwitch ? "⛔ KILL SWITCH ON" : "●";

  return (
    <Box flexDirection="column" height="100%">
      {/* Header bar */}
      <Box borderStyle="single" paddingX={1}>
        <Text color="cyan" bold>AlgoChains</Text>
        <Text color="gray">  |  </Text>
        <Text color="yellow">profile: {state.profileName}</Text>
        <Text color="gray">  |  </Text>
        <Text>regime: </Text><Text color="magenta">{state.regime}</Text>
        <Text color="gray">  |  </Text>
        <Text color={killColor}>{killLabel}</Text>
        <Text color="gray">  |  </Text>
        <Text color="gray">calls: {state.sessionCalls}  est: ${state.sessionCost.toFixed(3)}</Text>
      </Box>

      {/* Output area */}
      <Box flexDirection="column" flexGrow={1} overflowY="hidden" paddingX={1}>
        {state.output.slice(-30).map((line, i) => (
          <Text
            key={i}
            color={line.type === "user" ? "cyan" : line.type === "error" ? "red" : line.type === "system" ? "gray" : line.type === "info" ? "yellow" : undefined}
          >
            {line.text}
          </Text>
        ))}
        {state.loading && (
          <Text color="yellow">⟳  {state.loadingTool}...</Text>
        )}
      </Box>

      {/* Input bar */}
      <Box borderStyle="single" paddingX={1}>
        <Text color="cyan">{">"} </Text>
        <Text>
          {state.input.slice(0, state.cursor)}
          <Text inverse>{state.input[state.cursor] ?? " "}</Text>
          {state.input.slice(state.cursor + 1)}
        </Text>
      </Box>
    </Box>
  );
}

// ── Slash command handler ─────────────────────────────────────────────────────
function handleSlashCommand(
  cmd: string,
  ctx: { dispatch: React.Dispatch<ReplAction>; exit: () => void; mcp: ReturnType<typeof createMcpClient>; config: ReturnType<typeof loadConfig>; profile: ReturnType<typeof getActiveProfile> }
): void {
  const parts = cmd.split(" ");
  const slash = parts[0].toLowerCase();

  const info = (text: string) => ctx.dispatch({ type: "ADD_OUTPUT", text, kind: "info" } as Parameters<typeof ctx.dispatch>[0]);
  const err  = (text: string) => ctx.dispatch({ type: "ADD_OUTPUT", text, kind: "error" } as Parameters<typeof ctx.dispatch>[0]);

  switch (slash) {
    case "/help":
      info(`AlgoChains REPL — slash commands:
  /help           this message
  /tools [query]  discover tools by keyword
  /cost           show session cost and call count
  /history [n]    show last n commands (default 20)
  /profile        show active profile
  /kill on|off    toggle kill switch
  /doctor         run health checks
  /clear          clear output
  /exit           exit REPL

Tool calls: just type the tool name + args
  detect-market-regime
  get-bot-health
  discover-tools --query portfolio
  run-backtest --strategy RSI --symbol AAPL
  {"tool": "get_positions", "args": {}}
`);
      break;
    case "/tools": {
      const q = parts.slice(1).join(" ");
      ctx.mcp.callTool("discover_tools", { query: q || "all tools", top_k: 15 })
        .then(r => info(extractText(r)))
        .catch(e => err(String(e)));
      break;
    }
    case "/cost":
      info(`Session: ${ctx.dispatch.length} calls  |  Estimated: $${(0).toFixed(3)}`);
      break;
    case "/history": {
      const n = parseInt(parts[1] ?? "20", 10);
      const hist = loadCommandHistory().slice(-n);
      info(hist.length ? hist.join("\n") : "(no history)");
      break;
    }
    case "/profile":
      info(`Active profile: ${ctx.config.default.profile}\nMode: ${ctx.profile.mode}\nTool mode: ${ctx.profile.tool_mode}`);
      break;
    case "/kill":
      if (parts[1] === "on") {
        const { enableKillSwitch } = require("../trust.js");
        enableKillSwitch("REPL /kill on");
        ctx.dispatch({ type: "SET_KILL_SWITCH", active: true });
        info("🛑 Kill switch activated — T2/T3 tools blocked");
      } else if (parts[1] === "off") {
        const { disableKillSwitch } = require("../trust.js");
        disableKillSwitch();
        ctx.dispatch({ type: "SET_KILL_SWITCH", active: false });
        info("✓ Kill switch deactivated");
      } else {
        info(`Kill switch: ${isKillSwitchActive() ? "ACTIVE" : "inactive"}`);
      }
      break;
    case "/doctor":
      info("Running doctor checks...");
      import("../commands/doctor.js").then(({ runDoctorCheck, printDoctorResults }) => {
        runDoctorCheck(undefined, true).then(results => {
          for (const r of results) {
            const sym = r.status === "ok" ? "✓" : r.status === "warn" ? "⚠" : r.status === "skip" ? "–" : "✗";
            info(`${sym}  ${r.name.padEnd(20)} ${r.message}`);
          }
        });
      });
      break;
    case "/clear":
      ctx.dispatch({ type: "CLEAR" });
      break;
    case "/exit":
    case "/quit":
      ctx.exit();
      break;
    default:
      err(`Unknown slash command: ${slash}  (try /help)`);
  }
}

// ── History persistence ───────────────────────────────────────────────────────
function loadCommandHistory(): string[] {
  if (!existsSync(HISTORY_FILE)) return [];
  try {
    return readFileSync(HISTORY_FILE, "utf-8")
      .trim().split("\n")
      .map(line => { try { return JSON.parse(line).command as string; } catch { return null; } })
      .filter(Boolean) as string[];
  } catch { return []; }
}

function saveCommandHistory(command: string, existing: string[]): void {
  if (existing[existing.length - 1] === command) return; // no duplicates
  const entry = JSON.stringify({ command, ts: Date.now() });
  appendFileSync(HISTORY_FILE, entry + "\n", { mode: 0o600 });
}

// ── Arg parser ("--key value" → {key: value}) ─────────────────────────────────
function parseKeyValueArgs(parts: string[]): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].startsWith("--")) {
      const key = parts[i].slice(2).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      const val = parts[i + 1] ?? "true";
      result[key] = val === "true" ? true : val === "false" ? false : isNaN(+val) ? val : +val;
      i++;
    }
  }
  return result;
}
