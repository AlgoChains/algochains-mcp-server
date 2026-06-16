import assert from "node:assert/strict";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const home = join(tmpdir(), `algochains-cli-retry-${process.pid}`);
rmSync(home, { recursive: true, force: true });
mkdirSync(home, { recursive: true });
process.env.HOME = home;
process.env.USERPROFILE = home;

const retry = await import("../src/triggers/retry.ts");

test("isRetryableConnectionError detects transient transport failures", () => {
  assert.equal(retry.isRetryableConnectionError(new Error("connect ECONNREFUSED 127.0.0.1:8090")), true);
  assert.equal(retry.isRetryableConnectionError(new Error("ConnectTimeout: [Errno 60] Operation timed out")), true);
  assert.equal(retry.isRetryableConnectionError(new Error("HTTP 503 Service Unavailable")), true);
  assert.equal(retry.isRetryableConnectionError(new Error("invalid strategy config")), false);
});

test("backoffMs doubles up to the 60s cap", () => {
  assert.equal(retry.backoffMs(1), 1_000);
  assert.equal(retry.backoffMs(2), 2_000);
  assert.equal(retry.backoffMs(3), 4_000);
  assert.equal(retry.backoffMs(7), 60_000);
});

test("runCronRetries reports healthy empty queue", async () => {
  const result = await retry.runCronRetries(async () => {});
  assert.equal(result.status, "ok");
  assert.deepEqual(result.lines, ["[OK] No pending cron retries"]);
  assert.equal(result.pending_count, 0);
});

test("runCronRetries waits until backoff expires", async () => {
  retry.enqueueTriggerRetry("abc12345", "detect-market-regime --json", new Error("connect ECONNREFUSED"), 1);

  const result = await retry.runCronRetries(async () => {
    throw new Error("should not execute yet");
  });

  assert.equal(result.status, "wait");
  assert.equal(result.pending_count, 1);
  assert.match(result.lines[0], /^\[WAIT\] trigger abc12345 retry in \d+s \(attempt 1\/5\)$/);
});

test("runCronRetries executes due entries and clears the queue on success", async () => {
  const future = new Date(Date.now() - 1_000).toISOString();
  writeFileSync(
    retry.CRON_RETRIES_FILE,
    JSON.stringify([
      {
        trigger_id: "due12345",
        command: "detect-market-regime --json",
        attempt: 2,
        max_attempts: 5,
        next_retry_at: future,
        last_error: "connect ECONNREFUSED",
        enqueued_at: future,
      },
    ], null, 2),
    { mode: 0o600 },
  );

  const calls = [];
  const result = await retry.runCronRetries(async (triggerId, command) => {
    calls.push(`${triggerId}:${command}`);
  });

  assert.equal(result.status, "retry");
  assert.deepEqual(calls, ["due12345:detect-market-regime --json"]);
  assert.equal(retry.listPendingRetries().length, 0);
  assert.match(result.lines[0], /^\[RETRY\] trigger due12345 attempt 2\/5:/);
});

test("runCronRetries requeues retryable failures with incremented attempt", async () => {
  const future = new Date(Date.now() - 1_000).toISOString();
  writeFileSync(
    retry.CRON_RETRIES_FILE,
    JSON.stringify([
      {
        trigger_id: "fail1234",
        command: "get-bot-health --json",
        attempt: 1,
        max_attempts: 5,
        next_retry_at: future,
        last_error: "connect ECONNREFUSED",
        enqueued_at: future,
      },
    ], null, 2),
    { mode: 0o600 },
  );

  const result = await retry.runCronRetries(async () => {
    throw new Error("connect ECONNREFUSED 127.0.0.1:8090");
  });

  assert.equal(result.status, "wait");
  assert.equal(result.pending_count, 1);
  assert.equal(retry.listPendingRetries()[0]?.attempt, 2);
  assert.match(result.lines[0], /^\[RETRY\] trigger fail1234 attempt 1\/5:/);
  assert.match(result.lines[1], /^\[WAIT\] trigger fail1234 retry in \d+s \(attempt 2\/5\)$/);
});

test("runCronRetries drops exhausted retryable failures", async () => {
  const future = new Date(Date.now() - 1_000).toISOString();
  writeFileSync(
    retry.CRON_RETRIES_FILE,
    JSON.stringify([
      {
        trigger_id: "deadbeef",
        command: "get-bot-health --json",
        attempt: 5,
        max_attempts: 5,
        next_retry_at: future,
        last_error: "connect ECONNREFUSED",
        enqueued_at: future,
      },
    ], null, 2),
    { mode: 0o600 },
  );

  const result = await retry.runCronRetries(async () => {
    throw new Error("connect ECONNREFUSED 127.0.0.1:8090");
  });

  assert.equal(result.status, "failed");
  assert.equal(retry.listPendingRetries().length, 0);
  assert.match(result.lines[1], /^\[FAILED\] trigger deadbeef after 5 attempts:/);
});

test("cron retry state file is created under ~/.algochains", () => {
  assert.equal(existsSync(retry.CRON_RETRIES_FILE), true);
  assert.match(retry.CRON_RETRIES_FILE, /\.algochains\/cron_retries\.json$/);
});
