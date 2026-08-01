/**
 * AlgoChains CLI — Trigger Manager
 *
 * Trigger types:
 *   cron      — schedule: "0 9 * * 1-5" + command
 *   watch     — file path + command (runs when file changes)
 *   webhook   — HTTP POST endpoint path + command
 *   datetime  — one-time ISO datetime + command
 *
 * All triggers are stored in ~/.algochains/triggers.json.
 * The daemon evaluates active triggers when running.
 * T3 triggers always check kill switch before executing.
 */
import { existsSync, readFileSync, writeFileSync } from "fs";
import { randomUUID } from "crypto";
import { TRIGGERS_FILE } from "../config.js";
import { getTier, isKillSwitchActive } from "../trust.js";
import { enqueueTriggerRetry, isRetryableConnectionError, removeTriggerRetry } from "./retry.js";

export type TriggerType = "cron" | "watch" | "webhook" | "datetime";

export interface Trigger {
  id: string;
  type: TriggerType;
  schedule?: string;        // cron expression
  path?: string;            // file/dir to watch
  endpoint?: string;        // webhook URL path (e.g. /signals)
  datetime?: string;        // ISO datetime for one-time trigger
  command: string;          // CLI command to run
  enabled: boolean;
  created_at: string;
  last_run?: string;
  run_count: number;
  last_error?: string;
}

// ── Persistence ────────────────────────────────────────────────────────────────
function loadTriggers(): Trigger[] {
  if (!existsSync(TRIGGERS_FILE)) return [];
  try { return JSON.parse(readFileSync(TRIGGERS_FILE, "utf-8")); } catch { return []; }
}

function saveTriggers(triggers: Trigger[]): void {
  writeFileSync(TRIGGERS_FILE, JSON.stringify(triggers, null, 2), { mode: 0o600 });
}

// ── Add ────────────────────────────────────────────────────────────────────────
export function addTrigger(
  type: TriggerType,
  commandOrConfig: string,
  opts: { schedule?: string; path?: string; endpoint?: string; datetime?: string }
): Trigger {
  const triggers = loadTriggers();

  // Infer safety tier of the command being triggered
  const cmdTier = getTier(commandOrConfig.split(" ")[0]);
  if (cmdTier === "T3" && !process.env.ALGOCHAINS_ALLOW_T3_TRIGGERS) {
    console.warn(`  ⚠  T3 (live) trigger created. Will be blocked if kill switch is active.`);
  }

  const trigger: Trigger = {
    id: randomUUID().slice(0, 8),
    type,
    command: commandOrConfig,
    enabled: true,
    created_at: new Date().toISOString(),
    run_count: 0,
    ...opts,
  };

  triggers.push(trigger);
  saveTriggers(triggers);
  return trigger;
}

// ── List ───────────────────────────────────────────────────────────────────────
export function listTriggers(): Trigger[] {
  return loadTriggers();
}

// ── Enable/disable ────────────────────────────────────────────────────────────
export function setTriggerEnabled(id: string, enabled: boolean): void {
  const triggers = loadTriggers();
  const t = triggers.find(t => t.id === id);
  if (!t) throw new Error(`Trigger not found: ${id}`);
  t.enabled = enabled;
  saveTriggers(triggers);
}

// ── Remove ────────────────────────────────────────────────────────────────────
export function removeTrigger(id: string): void {
  const triggers = loadTriggers().filter(t => t.id !== id);
  saveTriggers(triggers);
}

// ── Execute a trigger command (with kill switch check) ────────────────────────
export async function executeTrigger(trigger: Trigger): Promise<void> {
  const cmdTier = getTier(trigger.command.split(" ")[0]);

  if ((cmdTier === "T2" || cmdTier === "T3") && isKillSwitchActive()) {
    const msg = `Kill switch is active — skipping T${cmdTier.slice(1)} trigger: ${trigger.id}`;
    console.warn(`  ⚠  ${msg}`);
    return;
  }

  const { execSync } = await import("child_process");
  const triggers = loadTriggers();
  const idx = triggers.findIndex(t => t.id === trigger.id);

  try {
    // Execute the CLI command (self-invocation)
    const cliCmd = `${process.argv[0]} ${process.argv[1]} ${trigger.command}`;
    execSync(cliCmd, { stdio: "inherit", timeout: 120_000 });

    if (idx >= 0) {
      triggers[idx].last_run = new Date().toISOString();
      triggers[idx].run_count = (triggers[idx].run_count ?? 0) + 1;
      delete triggers[idx].last_error;
      saveTriggers(triggers);
    }
  } catch (e) {
    if (idx >= 0) {
      triggers[idx].last_error = String(e);
      triggers[idx].last_run = new Date().toISOString();
      saveTriggers(triggers);
    }
    if (isRetryableConnectionError(e)) {
      enqueueTriggerRetry(trigger.id, trigger.command, e);
    }
    throw e;
  }
}

/** Execute a trigger by ID (used by cron retry handler). */
export async function executeTriggerById(triggerId: string): Promise<void> {
  const trigger = loadTriggers().find((entry) => entry.id === triggerId);
  if (!trigger) {
    throw new Error(`Trigger not found: ${triggerId}`);
  }
  await executeTrigger(trigger);
  removeTriggerRetry(triggerId);
}

// ── Start all triggers (called by daemon) ─────────────────────────────────────
export async function startTriggerLoop(): Promise<void> {
  const { default: cron } = await import("node-cron");
  const { watch } = await import("chokidar");

  const triggers = loadTriggers().filter(t => t.enabled);

  for (const trigger of triggers) {
    switch (trigger.type) {
      case "cron": {
        if (!trigger.schedule) continue;
        cron.schedule(trigger.schedule, async () => {
          console.log(`[trigger:${trigger.id}] Running cron: ${trigger.command}`);
          await executeTrigger(trigger).catch(e => console.error(`[trigger:${trigger.id}] Error: ${e}`));
        });
        console.log(`  ✓ Cron trigger ${trigger.id}: ${trigger.schedule} → ${trigger.command}`);
        break;
      }
      case "watch": {
        if (!trigger.path) continue;
        watch(trigger.path, { ignoreInitial: true }).on("change", async (path) => {
          console.log(`[trigger:${trigger.id}] File changed: ${path} → ${trigger.command}`);
          await executeTrigger(trigger).catch(e => console.error(`[trigger:${trigger.id}] Error: ${e}`));
        });
        console.log(`  ✓ Watch trigger ${trigger.id}: ${trigger.path} → ${trigger.command}`);
        break;
      }
      case "datetime": {
        if (!trigger.datetime) continue;
        const fireAt = new Date(trigger.datetime).getTime();
        const delay = fireAt - Date.now();
        if (delay > 0) {
          setTimeout(async () => {
            console.log(`[trigger:${trigger.id}] Datetime trigger fired: ${trigger.command}`);
            await executeTrigger(trigger).catch(e => console.error(`[trigger:${trigger.id}] Error: ${e}`));
            // Disable after one-shot
            setTriggerEnabled(trigger.id, false);
          }, delay);
          console.log(`  ✓ Datetime trigger ${trigger.id}: ${trigger.datetime} → ${trigger.command}`);
        } else {
          console.log(`  – Datetime trigger ${trigger.id} already passed: ${trigger.datetime}`);
        }
        break;
      }
      case "webhook": {
        // Webhook triggers are registered with the Hono server in daemon.ts
        // The endpoint is: POST /webhooks/{trigger.endpoint}
        console.log(`  ✓ Webhook trigger ${trigger.id}: POST /webhooks${trigger.endpoint} → ${trigger.command}`);
        break;
      }
    }
  }
}

// ── Print triggers ─────────────────────────────────────────────────────────────
export function printTriggerList(): void {
  const triggers = loadTriggers();
  if (triggers.length === 0) {
    console.log("  No triggers configured");
    console.log('  Add: algochains trigger add cron "0 9 * * 1-5" "detect-market-regime --json"');
    return;
  }

  console.log(`\n  Configured triggers (${triggers.length}):\n`);
  for (const t of triggers) {
    const status = t.enabled ? "\x1b[32m●\x1b[0m" : "\x1b[90m○\x1b[0m";
    const schedule =
      t.type === "cron"     ? `cron(${t.schedule})` :
      t.type === "watch"    ? `watch(${t.path})` :
      t.type === "webhook"  ? `POST /webhooks${t.endpoint}` :
      t.type === "datetime" ? `at(${t.datetime})` : t.type;

    console.log(`  ${status}  [${t.id}]  ${t.type.padEnd(8)}  ${schedule}`);
    console.log(`          command:  ${t.command}`);
    if (t.last_run) console.log(`          last run: ${t.last_run} (×${t.run_count})`);
    if (t.last_error) console.log(`          error:    \x1b[31m${t.last_error.slice(0, 80)}\x1b[0m`);
    console.log("");
  }
}
