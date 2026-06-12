import assert from "node:assert/strict";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

const home = join(tmpdir(), `algochains-cli-security-${process.pid}`);
rmSync(home, { recursive: true, force: true });
mkdirSync(home, { recursive: true });
process.env.HOME = home;
process.env.USERPROFILE = home;

const daemon = await import("../src/commands/daemon.ts");
const plugins = await import("../src/plugins/manager.ts");
const trust = await import("../src/trust.ts");
const config = await import("../src/config.ts");

test("daemon tool proxy requires a presented matching credential", () => {
  assert.equal(daemon.isAuthorizedDaemonRequest(undefined, undefined, "daemon-token", "bridge-key"), false);
  assert.equal(daemon.isAuthorizedDaemonRequest("Bearer daemon-token", undefined, "daemon-token", "bridge-key"), true);
  assert.equal(daemon.isAuthorizedDaemonRequest("prefix-daemon-token-suffix", undefined, "daemon-token", "bridge-key"), false);
  assert.equal(daemon.isAuthorizedDaemonRequest("Bearer wrong", undefined, "daemon-token", "bridge-key"), false);
  assert.equal(daemon.isAuthorizedDaemonRequest(undefined, "bridge-key", "daemon-token", "bridge-key"), true);
  assert.equal(daemon.isAuthorizedDaemonRequest("Bearer bridge-key", undefined, "daemon-token", "bridge-key"), true);
});

test("plugin paths stay inside the configured plugins directory", () => {
  const pluginsRoot = resolve(config.PLUGINS_DIR);
  assert.equal(plugins.resolvePluginDir("@algochains/plugin-kalshi"), join(pluginsRoot, "kalshi"));

  assert.throws(() => plugins.resolvePluginDir("../../victim"), /Invalid plugin name/);
  assert.throws(() => plugins.resolvePluginDir("@algochains/plugin-../../victim"), /Invalid plugin name/);
  assert.throws(() => plugins.resolvePluginDir("/tmp/victim"), /Invalid plugin name/);
});

test("plugin remove cannot delete escaped paths", () => {
  const pluginDir = plugins.resolvePluginDir("kalshi");
  mkdirSync(pluginDir, { recursive: true });
  writeFileSync(join(pluginDir, "plugin.json"), "{}");

  const victim = join(home, "victim");
  mkdirSync(victim, { recursive: true });
  writeFileSync(join(victim, "keep.txt"), "do not delete");

  assert.throws(() => plugins.removePlugin("../../victim"), /Invalid plugin name/);
  assert.equal(existsSync(join(victim, "keep.txt")), true);

  plugins.removePlugin("kalshi");
  assert.equal(existsSync(pluginDir), false);
});

test("unmapped pass-through tools fail closed as T3", () => {
  assert.equal(trust.getTier("place-order"), "T2");
  assert.equal(trust.getTier("new-live-order-tool"), "T3");

  const blocked = trust.checkTrustGate({
    command: "new-live-order-tool",
    safeOnly: true,
    confirm: true,
    profile: "live",
  });
  assert.equal(blocked.allowed, false);
  assert.match(blocked.reason, /--safe-only blocks T3/);
});
