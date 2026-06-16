import assert from "node:assert/strict";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const home = join(tmpdir(), `algochains-cli-triggers-${process.pid}`);
rmSync(home, { recursive: true, force: true });
mkdirSync(home, { recursive: true });
process.env.HOME = home;
process.env.USERPROFILE = home;

const originalArgv = [...process.argv];
process.argv[0] = process.execPath;
process.argv[1] = "--eval";

const triggers = await import("../src/triggers/manager.ts");
const config = await import("../src/config.ts");

function readStoredTriggers() {
  return JSON.parse(readFileSync(config.TRIGGERS_FILE, "utf-8"));
}

test.after(() => {
  process.argv.splice(0, process.argv.length, ...originalArgv);
  rmSync(home, { recursive: true, force: true });
});

test("cron retry delay doubles until capped", () => {
  assert.equal(triggers.calculateCronRetryDelayMs(1, 1_000, 10_000), 1_000);
  assert.equal(triggers.calculateCronRetryDelayMs(2, 1_000, 10_000), 2_000);
  assert.equal(triggers.calculateCronRetryDelayMs(4, 1_000, 10_000), 8_000);
  assert.equal(triggers.calculateCronRetryDelayMs(5, 1_000, 10_000), 10_000);
});

test("failed cron command is retried and clears retry state on recovery", async () => {
  const trigger = triggers.addTrigger("cron", '"process.exit(1)"', { schedule: "* * * * *" });

  await assert.rejects(() => triggers.executeTrigger(trigger));

  let stored = readStoredTriggers();
  assert.equal(stored[0].id, trigger.id);
  assert.equal(stored[0].cron_retry_count, 1);
  assert.ok(Date.parse(stored[0].next_retry_at) > Date.now());
  assert.match(stored[0].last_error, /Command failed/);
  assert.equal(stored[0].run_count, 0);

  stored[0].command = '"process.exit(0)"';
  stored[0].next_retry_at = new Date(Date.now() - 1_000).toISOString();
  writeFileSync(config.TRIGGERS_FILE, JSON.stringify(stored, null, 2), { mode: 0o600 });

  await triggers.runDueCronRetries();

  stored = readStoredTriggers();
  assert.equal(stored[0].run_count, 1);
  assert.equal(stored[0].last_error, undefined);
  assert.equal(stored[0].cron_retry_count, undefined);
  assert.equal(stored[0].next_retry_at, undefined);
});
