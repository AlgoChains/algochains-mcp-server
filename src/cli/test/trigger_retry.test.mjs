import assert from "node:assert/strict";
import test from "node:test";

import {
  computeRetryDelaySec,
  formatCronRetryOutput,
  isRecoverableConnectionError,
} from "../src/triggers/retry.ts";

test("isRecoverableConnectionError detects transport failures", () => {
  assert.equal(isRecoverableConnectionError("Error: connect ECONNREFUSED 127.0.0.1:8090"), true);
  assert.equal(isRecoverableConnectionError("ConnectTimeout: [Errno 60] Operation timed out"), true);
  assert.equal(isRecoverableConnectionError("fetch failed"), true);
  assert.equal(isRecoverableConnectionError("Command failed: invalid strategy schema"), false);
});

test("computeRetryDelaySec grows exponentially and stays bounded", () => {
  assert.ok(computeRetryDelaySec(1) >= 1);
  assert.ok(computeRetryDelaySec(2) >= 2);
  assert.ok(computeRetryDelaySec(10) <= 60);
});

test("formatCronRetryOutput replaces bare SILENT with actionable status", () => {
  const idle = {
    status: "silent",
    pending_count: 0,
    due_count: 0,
    retried: 0,
    succeeded: 0,
    failed: 0,
    waiting: [],
    details: [],
  };
  assert.equal(formatCronRetryOutput(idle), "[OK] No pending cron retries");

  const waiting = {
    ...idle,
    status: "waiting",
    pending_count: 1,
    waiting: [{ id: "abc", command: "detect-market-regime --json", retry_count: 2, next_retry_at: "2026-06-16T17:00:00.000Z" }],
  };
  assert.match(formatCronRetryOutput(waiting), /^\[WAIT\]/);

  const retried = {
    ...idle,
    status: "retried",
    retried: 2,
    succeeded: 1,
    failed: 1,
  };
  assert.match(formatCronRetryOutput(retried), /^\[RETRY\]/);
});
