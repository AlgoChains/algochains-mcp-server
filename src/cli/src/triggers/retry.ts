/**
 * Cron trigger retry queue — exponential backoff on connection/recovery failures.
 *
 * CRON-RETRY watchdog calls `algochains trigger retry`. When the queue is empty
 * we emit `[OK] No pending cron retries` instead of bare SILENT output.
 */
import type { Trigger } from "./manager.js";
import { executeTrigger, listTriggers, saveTriggerRecord } from "./manager.js";

export const MAX_RETRY_ATTEMPTS = 5;
export const BASE_BACKOFF_MS = 1_000;
export const MAX_BACKOFF_MS = 60_000;

const CONNECTION_ERROR_PATTERNS = [
  /ECONNREFUSED/i,
  /ETIMEDOUT/i,
  /ENOTFOUND/i,
  /ECONNRESET/i,
  /EHOSTUNREACH/i,
  /ENETUNREACH/i,
  /ConnectError/i,
  /ConnectTimeout/i,
  /connection reset/i,
  /timed out/i,
  /fetch failed/i,
  /network/i,
  /socket hang up/i,
  /\b502\b/,
  /\b503\b/,
  /service unavailable/i,
];

export function isConnectionError(error: unknown): boolean {
  const msg = String(error ?? "");
  return CONNECTION_ERROR_PATTERNS.some((pattern) => pattern.test(msg));
}

export function computeBackoffMs(attempt: number): number {
  return Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
}

export function queueTriggerRetry(trigger: Trigger, error: unknown): void {
  const current = listTriggers().find((entry) => entry.id === trigger.id) ?? trigger;
  const retryCount = current.retry_count ?? 0;
  if (retryCount >= MAX_RETRY_ATTEMPTS) {
    saveTriggerRecord(current.id, {
      retry_pending: false,
      last_retry_error: String(error),
      last_error: String(error),
    });
    return;
  }

  saveTriggerRecord(current.id, {
    retry_pending: true,
    retry_count: retryCount,
    next_retry_at: new Date(Date.now() + computeBackoffMs(retryCount)).toISOString(),
    last_retry_error: String(error),
    last_error: String(error),
  });
}

export type RetryStatus = "ok" | "wait" | "retry" | "failed";

export interface RetrySummary {
  status: RetryStatus;
  messages: string[];
}

export async function retryPendingTriggers(
  now = Date.now(),
  executor: (trigger: Trigger) => Promise<void> = executeTrigger,
): Promise<RetrySummary> {
  const pending = listTriggers().filter((trigger) => trigger.retry_pending);
  if (pending.length === 0) {
    const message = "[OK] No pending cron retries";
    console.log(message);
    return { status: "ok", messages: [message] };
  }

  const messages: string[] = [];
  let sawRetry = false;
  let sawWait = false;
  let sawFailure = false;

  for (const trigger of pending) {
    const retryCount = trigger.retry_count ?? 0;
    const nextRetryAt = trigger.next_retry_at ? Date.parse(trigger.next_retry_at) : 0;

    if (Number.isFinite(nextRetryAt) && now < nextRetryAt) {
      const waitSec = Math.max(1, Math.ceil((nextRetryAt - now) / 1_000));
      const message = `[WAIT] ${trigger.id} next retry in ${waitSec}s (attempt ${retryCount + 1}/${MAX_RETRY_ATTEMPTS})`;
      console.log(message);
      messages.push(message);
      sawWait = true;
      continue;
    }

    const retryMessage = `[RETRY] ${trigger.id} attempt ${retryCount + 1}/${MAX_RETRY_ATTEMPTS}: ${trigger.command}`;
    console.log(retryMessage);
    messages.push(retryMessage);
    sawRetry = true;

    try {
      await executor(trigger);
      saveTriggerRecord(trigger.id, {
        retry_pending: false,
        retry_count: 0,
        next_retry_at: undefined,
        last_retry_error: undefined,
        last_error: undefined,
      });
      const okMessage = `[OK] ${trigger.id} recovered`;
      console.log(okMessage);
      messages.push(okMessage);
    } catch (error) {
      const nextCount = retryCount + 1;
      if (nextCount >= MAX_RETRY_ATTEMPTS || !isConnectionError(error)) {
        saveTriggerRecord(trigger.id, {
          retry_pending: false,
          retry_count: nextCount,
          next_retry_at: undefined,
          last_retry_error: String(error),
          last_error: String(error),
        });
        const failMessage = `[FAILED] ${trigger.id} after ${nextCount} attempts`;
        console.log(failMessage);
        messages.push(failMessage);
        sawFailure = true;
        continue;
      }

      saveTriggerRecord(trigger.id, {
        retry_pending: true,
        retry_count: nextCount,
        next_retry_at: new Date(now + computeBackoffMs(nextCount)).toISOString(),
        last_retry_error: String(error),
        last_error: String(error),
      });
      const waitMessage = `[WAIT] ${trigger.id} backing off (${Math.round(computeBackoffMs(nextCount) / 1_000)}s)`;
      console.log(waitMessage);
      messages.push(waitMessage);
      sawWait = true;
    }
  }

  if (sawFailure) return { status: "failed", messages };
  if (sawRetry) return { status: "retry", messages };
  if (sawWait) return { status: "wait", messages };
  return { status: "ok", messages };
}
