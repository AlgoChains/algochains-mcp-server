import assert from "node:assert/strict";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const home = join(tmpdir(), `algochains-cli-triggers-${process.pid}`);
rmSync(home, { recursive: true, force: true });
mkdirSync(home, { recursive: true });
process.env.HOME = home;
process.env.USERPROFILE = home;

const config = await import("../src/config.ts");
const triggers = await import("../src/triggers/manager.ts");

test("computeCronRetryDelayMs backs off exponentially with a cap", () => {
  assert.equal(triggers.computeCronRetryDelayMs(1, 1_000, 10_000), 1_000);
  assert.equal(triggers.computeCronRetryDelayMs(2, 1_000, 10_000), 2_000);
  assert.equal(triggers.computeCronRetryDelayMs(5, 1_000, 10_000), 10_000);
});

test("failed cron triggers are retried when due and clear retry state on success", async () => {
  const stateFile = join(home, "cron-state.txt");
  const runner = join(home, "fake-cli.mjs");
  writeFileSync(runner, `
import { existsSync, readFileSync, writeFileSync } from "node:fs";

const statePath = process.argv[process.argv.indexOf("--state") + 1];
const current = existsSync(statePath) ? Number.parseInt(readFileSync(statePath, "utf-8"), 10) : 0;
const next = current + 1;
writeFileSync(statePath, String(next));
process.exit(next === 1 ? 1 : 0);
`);

  const originalArgv1 = process.argv[1];
  process.argv[1] = runner;
  try {
    const trigger = triggers.addTrigger("cron", `--state ${stateFile}`, { schedule: "* * * * *" });

    await assert.rejects(() => triggers.executeTrigger(trigger));

    let persisted = triggers.listTriggers()[0];
    assert.equal(persisted.cron_retry_count, 1);
    assert.match(persisted.next_retry_at ?? "", /^\d{4}-\d{2}-\d{2}T/);
    assert.match(persisted.last_error ?? "", /Command failed/);

    persisted.next_retry_at = new Date(Date.now() - 1_000).toISOString();
    writeFileSync(config.TRIGGERS_FILE, JSON.stringify([persisted], null, 2), { mode: 0o600 });

    await triggers.runDueCronRetries(new Date());

    persisted = triggers.listTriggers()[0];
    assert.equal(readFileSync(stateFile, "utf-8"), "2");
    assert.equal(persisted.run_count, 1);
    assert.equal(persisted.last_error, undefined);
    assert.equal(persisted.cron_retry_count, undefined);
    assert.equal(persisted.next_retry_at, undefined);
  } finally {
    process.argv[1] = originalArgv1;
  }
});

test("trigger persistence creates the config directory", () => {
  rmSync(config.CONFIG_DIR, { recursive: true, force: true });

  triggers.addTrigger("cron", "doctor --quick", { schedule: "* * * * *" });

  assert.equal(existsSync(config.TRIGGERS_FILE), true);
});
