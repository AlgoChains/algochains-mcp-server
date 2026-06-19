/**
 * Cron trigger retry queue — exponential backoff for connection/recovery failures.
 *
 * Pending retries live in ~/.algochains/cron_retries.json.
 * The CRON-RETRY watchdog should call: algochains trigger retry
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { join } from "path";
import { CONFIG_DIR, ensureConfigDir } from "../config.js";

export const CRON_RETRIES_FILE = join(CONFIG_DIR, "cron_retries.json");

export const BASE_DELAY_MS = 1_000;
export const MAX_DELAY_MS = 60_000;
export const MAX_ATTEMPTS = 5;

export interface PendingRetry {
  trigger_id: string;
  command: string;
  attempt: number;
  max_attempts: number;
  next_retry_at: string;
  last_error: string;
  enqueued_at: string;
}

export type RetryRunStatus = "ok" | "wait" | "retry" | "failed";

export interface RetryRunResult {
  status: RetryRunStatus;
  lines: string[];
  pending_count: number;
}

const RETRYABLE_PATTERNS = [
  "econnrefused",
  "etimedout",
  "enotfound",
  "econnreset",
  "enetunreach",
  "connecttimeout",
  "connecterror",
  "connection reset",
  "connection refused",
  "operation timed out",
  "socket hang up",
  "network error",
  "fetch failed",
  "server unreachable",
  "503",
  "502",
  "504",
  "eai_again",
  "getaddrinfo",
];

function loadRetries(): PendingRetry[] {
  if (!existsSync(CRON_RETRIES_FILE)) return [];
  try {
    const parsed = JSON.parse(readFileSync(CRON_RETRIES_FILE, "utf-8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveRetries(retries: PendingRetry[]): void {
  ensureConfigDir();
  writeFileSync(CRON_RETRIES_FILE, JSON.stringify(retries, null, 2), { mode: 0o600 });
}

export function isRetryableConnectionError(error: unknown): boolean {
  const msg = String(error).toLowerCase();
  return RETRYABLE_PATTERNS.some((pattern) => msg.includes(pattern));
}

export function backoffMs(attempt: number): number {
  const exponent = Math.max(0, attempt - 1);
  return Math.min(BASE_DELAY_MS * 2 ** exponent, MAX_DELAY_MS);
}

export function listPendingRetries(): PendingRetry[] {
  return loadRetries();
}

export function enqueueTriggerRetry(
  triggerId: string,
  command: string,
  error: unknown,
  attempt = 1,
): PendingRetry {
  const retries = loadRetries().filter((entry) => entry.trigger_id !== triggerId);
  const now = Date.now();
  const entry: PendingRetry = {
    trigger_id: triggerId,
    command,
    attempt,
    max_attempts: MAX_ATTEMPTS,
    next_retry_at: new Date(now + backoffMs(attempt)).toISOString(),
    last_error: String(error).slice(0, 500),
    enqueued_at: new Date(now).toISOString(),
  };
  retries.push(entry);
  saveRetries(retries);
  return entry;
}

export function removeTriggerRetry(triggerId: string): void {
  const retries = loadRetries().filter((entry) => entry.trigger_id !== triggerId);
  saveRetries(retries);
}

function formatWaitLine(entry: PendingRetry, nowMs: number): string {
  const waitSec = Math.max(0, Math.ceil((Date.parse(entry.next_retry_at) - nowMs) / 1000));
  return `[WAIT] trigger ${entry.trigger_id} retry in ${waitSec}s (attempt ${entry.attempt}/${entry.max_attempts})`;
}

function formatRetryLine(entry: PendingRetry): string {
  return `[RETRY] trigger ${entry.trigger_id} attempt ${entry.attempt}/${entry.max_attempts}: ${entry.command}`;
}

function formatFailedLine(entry: PendingRetry): string {
  return `[FAILED] trigger ${entry.trigger_id} after ${entry.max_attempts} attempts: ${entry.last_error.slice(0, 120)}`;
}

export async function runCronRetries(
  execute: (triggerId: string, command: string) => Promise<void>,
): Promise<RetryRunResult> {
  const nowMs = Date.now();
  const retries = loadRetries();
  const lines: string[] = [];

  if (retries.length === 0) {
    return {
      status: "ok",
      lines: ["[OK] No pending cron retries"],
      pending_count: 0,
    };
  }

  let sawRetry = false;
  let sawFailed = false;
  const remaining: PendingRetry[] = [];

  for (const entry of retries) {
    const dueAt = Date.parse(entry.next_retry_at);
    if (Number.isNaN(dueAt) || dueAt > nowMs) {
      lines.push(formatWaitLine(entry, nowMs));
      remaining.push(entry);
      continue;
    }

    lines.push(formatRetryLine(entry));
    sawRetry = true;

    try {
      await execute(entry.trigger_id, entry.command);
      continue;
    } catch (error) {
      const nextAttempt = entry.attempt + 1;
      if (nextAttempt > entry.max_attempts || !isRetryableConnectionError(error)) {
        lines.push(formatFailedLine({ ...entry, last_error: String(error) }));
        sawFailed = true;
        continue;
      }

      const requeued: PendingRetry = {
        trigger_id: entry.trigger_id,
        command: entry.command,
        attempt: nextAttempt,
        max_attempts: entry.max_attempts,
        next_retry_at: new Date(Date.now() + backoffMs(nextAttempt)).toISOString(),
        last_error: String(error).slice(0, 500),
        enqueued_at: entry.enqueued_at,
      };
      lines.push(formatWaitLine(requeued, Date.now()));
      remaining.push(requeued);
    }
  }

  saveRetries(remaining);

  const status: RetryRunStatus = sawFailed
    ? "failed"
    : remaining.length > 0
      ? "wait"
      : sawRetry
        ? "retry"
        : "ok";

  return {
    status,
    lines,
    pending_count: remaining.length,
  };
}
