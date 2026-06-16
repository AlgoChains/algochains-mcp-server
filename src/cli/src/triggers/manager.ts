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
import {
  computeNextRetryAt,
  formatCronRetryOutput,
  isRecoverableConnectionError,
  MAX_CRON_RETRY_ATTEMPTS,
  type CronRetryResult,
} from "./retry.js";

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
  /** Set after a connection/recovery failure; cleared on success. */
  pending_retry?: boolean;
  retry_count?: number;
  next_retry_at?: string;
}

// ── Persistence ────────────────────────────────────────────────────────────────
function loadTriggers(): Trigger[] {
  if (!existsSync(TRIGGERS_FILE)) return [];
  try { return JSON.parse(readFileSync(TRIGGERS_FILE, "utf-8")); } catch { return []; }
}

function saveTriggers(triggers: Trigger[]): void {
  writeFileSync(TRIGGERS_FILE, JSON.stringify(triggers, null, 2), { mode: 0o600 });
}

function clearRetryState(trigger: Trigger): void {
  delete trigger.pending_retry;
  delete trigger.retry_count;
  delete trigger.next_retry_at;
}

function scheduleConnectionRetry(trigger: Trigger, errorMessage: string): void {
  if (!isRecoverableConnectionError(errorMessage)) {
    clearRetryState(trigger);
    return;
  }
  const retryCount = (trigger.retry_count ?? 0) + 1;
  if (retryCount > MAX_CRON_RETRY_ATTEMPTS) {
    trigger.pending_retry = false;
    trigger.retry_count = retryCount;
    delete trigger.next_retry_at;
    return;
  }
  trigger.pending_retry = true;
  trigger.retry_count = retryCount;
  trigger.next_retry_at = computeNextRetryAt(retryCount);
}

function normalizeLegacyRetryQueue(triggers: Trigger[]): void {
  const now = Date.now();
  for (const trigger of triggers) {
    if (!trigger.enabled || trigger.pending_retry || !trigger.last_error) continue;
    if (!isRecoverableConnectionError(trigger.last_error)) continue;
    trigger.pending_retry = true;
    trigger.retry_count = trigger.retry_count ?? 1;
    trigger.next_retry_at = trigger.next_retry_at ?? new Date(now).toISOString();
  }
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
      clearRetryState(triggers[idx]);
      saveTriggers(triggers);
    }
  } catch (e) {
    if (idx >= 0) {
      const message = String(e);
      triggers[idx].last_error = message;
      triggers[idx].last_run = new Date().toISOString();
      scheduleConnectionRetry(triggers[idx], message);
      saveTriggers(triggers);
    }
    throw e;
  }
}

// ── Retry failed cron triggers (connection recovery) ───────────────────────────
export async function retryFailedTriggers(): Promise<CronRetryResult> {
  const triggers = loadTriggers();
  normalizeLegacyRetryQueue(triggers);
  saveTriggers(triggers);

  const now = Date.now();
  const pending = triggers.filter(t => t.enabled && t.pending_retry);
  const waiting = pending
    .filter(t => t.next_retry_at && Date.parse(t.next_retry_at) > now)
    .map(t => ({
      id: t.id,
      command: t.command,
      retry_count: t.retry_count ?? 0,
      next_retry_at: t.next_retry_at!,
    }))
    .sort((a, b) => Date.parse(a.next_retry_at) - Date.parse(b.next_retry_at));

  const due = pending.filter(t => !t.next_retry_at || Date.parse(t.next_retry_at) <= now);
  const result: CronRetryResult = {
    status: "silent",
    pending_count: pending.length,
    due_count: due.length,
    retried: 0,
    succeeded: 0,
    failed: 0,
    waiting,
    details: [],
  };

  for (const trigger of due) {
    result.retried += 1;
    try {
      await executeTrigger(trigger);
      result.succeeded += 1;
      result.details.push({ id: trigger.id, command: trigger.command, outcome: "ok" });
    } catch (e) {
      result.failed += 1;
      result.details.push({
        id: trigger.id,
        command: trigger.command,
        outcome: "failed",
        error: String(e).slice(0, 200),
      });
    }
  }

  if (result.retried > 0) {
    result.status = "retried";
  } else if (result.pending_count > 0) {
    result.status = "waiting";
  }
  return result;
}

export function printCronRetryResult(result: CronRetryResult, json = false): void {
  if (json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }
  console.log(formatCronRetryOutput(result));
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
          await executeTrigger(trigger).catch(e => {
            const msg = String(e);
            console.error(`[trigger:${trigger.id}] Error: ${msg}`);
            if (isRecoverableConnectionError(msg)) {
              console.error(`[trigger:${trigger.id}] Queued for connection-recovery retry`);
            }
          });
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
    if (t.pending_retry && t.next_retry_at) {
      console.log(`          retry:    queued (attempt ${t.retry_count ?? 0}, after ${t.next_retry_at})`);
    }
    console.log("");
  }
}
