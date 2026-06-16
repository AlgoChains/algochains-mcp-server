import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import test from "node:test";

const home = join(tmpdir(), `algochains-trigger-retry-${process.pid}`);
rmSync(home, { recursive: true, force: true });
mkdirSync(home, { recursive: true });
process.env.HOME = home;
process.env.USERPROFILE = home;

const config = await import("../src/config.ts");
const retry = await import("../src/triggers/retry.ts");

function writeTrigger(trigger) {
  mkdirSync(dirname(config.TRIGGERS_FILE), { recursive: true, mode: 0o700 });
  writeFileSync(config.TRIGGERS_FILE, JSON.stringify([trigger], null, 2), { mode: 0o600 });
}

function readTrigger() {
  return JSON.parse(readFileSync(config.TRIGGERS_FILE, "utf-8"))[0];
}

test("computeBackoffMs grows exponentially up to 60s", () => {
  assert.equal(retry.computeBackoffMs(0), 1_000);
  assert.equal(retry.computeBackoffMs(1), 2_000);
  assert.equal(retry.computeBackoffMs(2), 4_000);
  assert.equal(retry.computeBackoffMs(5), 32_000);
  assert.equal(retry.computeBackoffMs(10), 60_000);
});

test("isConnectionError recognizes transport failures", () => {
  assert.equal(retry.isConnectionError(new Error("connect ECONNREFUSED 127.0.0.1:8090")), true);
  assert.equal(retry.isConnectionError("ConnectTimeout: [Errno 60] Operation timed out"), true);
  assert.equal(retry.isConnectionError(new Error("invalid strategy config")), false);
});

test("retryPendingTriggers reports OK when queue is empty", async () => {
  writeTrigger({
    id: "cron-empty",
    type: "cron",
    schedule: "* * * * *",
    command: "detect-market-regime --json",
    enabled: true,
    created_at: new Date().toISOString(),
    run_count: 0,
  });

  const summary = await retry.retryPendingTriggers();
  assert.equal(summary.status, "ok");
  assert.deepEqual(summary.messages, ["[OK] No pending cron retries"]);
});

test("queueTriggerRetry schedules pending trigger with backoff", () => {
  writeTrigger({
    id: "cron-1",
    type: "cron",
    schedule: "* * * * *",
    command: "detect-market-regime --json",
    enabled: true,
    created_at: new Date().toISOString(),
    run_count: 0,
  });

  retry.queueTriggerRetry(readTrigger(), new Error("connect ECONNREFUSED 127.0.0.1:8090"));
  const queued = readTrigger();
  assert.equal(queued.retry_pending, true);
  assert.equal(queued.retry_count, 0);
  assert.ok(queued.next_retry_at);
});

test("retryPendingTriggers waits until next_retry_at", async () => {
  const future = new Date(Date.now() + 60_000).toISOString();
  writeTrigger({
    id: "cron-2",
    type: "cron",
    schedule: "* * * * *",
    command: "get-bot-health --json",
    enabled: true,
    created_at: new Date().toISOString(),
    run_count: 1,
    retry_pending: true,
    retry_count: 1,
    next_retry_at: future,
  });

  const summary = await retry.retryPendingTriggers();
  assert.equal(summary.status, "wait");
  assert.match(summary.messages[0], /\[WAIT\] cron-2 next retry in \d+s/);
});

test("retryPendingTriggers clears queue after successful retry", async () => {
  writeTrigger({
    id: "cron-3",
    type: "cron",
    schedule: "* * * * *",
    command: "get-bot-health --json",
    enabled: true,
    created_at: new Date().toISOString(),
    run_count: 2,
    retry_pending: true,
    retry_count: 1,
    next_retry_at: new Date(Date.now() - 1_000).toISOString(),
  });

  const summary = await retry.retryPendingTriggers(Date.now(), async () => {});
  assert.equal(summary.status, "retry");
  assert.match(summary.messages.join("\n"), /\[RETRY\] cron-3 attempt 2\/5/);
  assert.match(summary.messages.join("\n"), /\[OK\] cron-3 recovered/);

  const cleared = readTrigger();
  assert.equal(cleared.retry_pending, false);
  assert.equal(cleared.retry_count, 0);
  assert.equal(cleared.next_retry_at, undefined);
});

test("retryPendingTriggers backs off after another connection failure", async () => {
  writeTrigger({
    id: "cron-4",
    type: "cron",
    schedule: "* * * * *",
    command: "get-bot-health --json",
    enabled: true,
    created_at: new Date().toISOString(),
    run_count: 0,
    retry_pending: true,
    retry_count: 0,
    next_retry_at: new Date(Date.now() - 1_000).toISOString(),
  });

  const summary = await retry.retryPendingTriggers(
    Date.now(),
    async () => {
      throw new Error("connect ECONNREFUSED 127.0.0.1:8090");
    },
  );

  assert.equal(summary.status, "retry");
  assert.match(summary.messages.join("\n"), /\[RETRY\] cron-4 attempt 1\/5/);
  assert.match(summary.messages.join("\n"), /\[WAIT\] cron-4 backing off \(2s\)/);

  const queued = readTrigger();
  assert.equal(queued.retry_pending, true);
  assert.equal(queued.retry_count, 1);
});

test("triggers file is created under ~/.algochains", () => {
  assert.equal(existsSync(config.TRIGGERS_FILE), true);
});
