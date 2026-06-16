/**
 * Cron trigger retry — exponential backoff after connection/recovery failures.
 * Used by `algochains trigger retry` (CRON-RETRY watchdog integration).
 */

export const MAX_CRON_RETRY_ATTEMPTS = 5;
export const MAX_CRON_RETRY_DELAY_SEC = 60;

const CONNECTION_ERROR_RE =
  /ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|ECONNABORTED|socket hang up|fetch failed|connection reset|connection refused|network (?:error|unreachable)|operation timed out|ConnectError|ConnectTimeout|ConnectionError|503|502|504/i;

export function isRecoverableConnectionError(message: string): boolean {
  return CONNECTION_ERROR_RE.test(message);
}

/** Exponential backoff with jitter: 1s, 2s, 4s, … capped at 60s. */
export function computeRetryDelaySec(retryCount: number): number {
  const attempt = Math.max(1, retryCount);
  const base = Math.min(MAX_CRON_RETRY_DELAY_SEC, 2 ** (attempt - 1));
  const jitter = Math.random() * 0.25 * base;
  const delay = base + jitter;
  return Math.round(Math.min(MAX_CRON_RETRY_DELAY_SEC, delay) * 1000) / 1000;
}

export function computeNextRetryAt(retryCount: number, nowMs: number = Date.now()): string {
  const delayMs = computeRetryDelaySec(retryCount) * 1000;
  return new Date(nowMs + delayMs).toISOString();
}

export interface CronRetryResult {
  status: "silent" | "retried" | "waiting";
  pending_count: number;
  due_count: number;
  retried: number;
  succeeded: number;
  failed: number;
  waiting: Array<{ id: string; command: string; retry_count: number; next_retry_at: string }>;
  details: Array<{ id: string; command: string; outcome: "ok" | "failed" | "skipped"; error?: string }>;
}

export function formatCronRetryOutput(result: CronRetryResult): string {
  if (result.retried > 0) {
    const parts = [`[RETRY] ${result.succeeded}/${result.retried} cron trigger(s) recovered`];
    if (result.failed > 0) {
      parts.push(`${result.failed} still failing`);
    }
    if (result.pending_count > result.due_count) {
      parts.push(`${result.pending_count - result.due_count} waiting for backoff`);
    }
    return parts.join("; ");
  }
  if (result.pending_count > 0) {
    const next = result.waiting[0]?.next_retry_at ?? "unknown";
    return `[WAIT] ${result.pending_count} cron trigger(s) queued; next retry after ${next}`;
  }
  return "[OK] No pending cron retries";
}
