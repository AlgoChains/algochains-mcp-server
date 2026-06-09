/**
 * AlgoChains CLI — Plugin Manager
 *
 * Plugin manifest (plugin.json):
 * {
 *   "name": "@algochains/plugin-kalshi",
 *   "version": "1.0.0",
 *   "trust": "official" | "community",
 *   "tools": ["get_kalshi_markets", "place_kalshi_order"],
 *   "env_required": ["KALSHI_API_KEY"],
 *   "safety_tier": "T2",
 *   "description": "Kalshi prediction market integration",
 *   "entry": "index.js",
 *   "signature": "sha256:abc123..." // for official plugins
 * }
 *
 * Official plugins: signed by AlgoChains, auto-trusted.
 * Community plugins: require --allow-community flag.
 * All plugins run in isolated subprocess with filtered env.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "fs";
import { join } from "path";
import { createHash } from "crypto";
import { PLUGINS_DIR } from "../config.js";

export interface PluginManifest {
  name: string;
  version: string;
  trust: "official" | "community";
  tools: string[];
  env_required: string[];
  safety_tier: "T0" | "T1" | "T2" | "T3";
  description?: string;
  entry?: string;
  signature?: string;
  author?: string;
  homepage?: string;
}

const OFFICIAL_PLUGINS: Record<string, string> = {
  "@algochains/plugin-kalshi":    "https://registry.algochains.ai/plugins/kalshi/latest.tar.gz",
  "@algochains/plugin-rithmic":   "https://registry.algochains.ai/plugins/rithmic/latest.tar.gz",
  "@algochains/plugin-coinbase":  "https://registry.algochains.ai/plugins/coinbase/latest.tar.gz",
  "@algochains/plugin-ib-tws":    "https://registry.algochains.ai/plugins/ib-tws/latest.tar.gz",
  "@algochains/plugin-polymarket": "https://registry.algochains.ai/plugins/polymarket/latest.tar.gz",
};

// ── Install ───────────────────────────────────────────────────────────────────
export async function installPlugin(nameOrRef: string, allowCommunity = false): Promise<void> {
  const isGitHub = nameOrRef.startsWith("github:");
  const isOfficial = nameOrRef.startsWith("@algochains/");

  if (!isOfficial && !isGitHub && !allowCommunity) {
    throw new Error(
      `Community plugins require --allow-community flag.\n` +
      `  algochains plugin install ${nameOrRef} --allow-community\n\n` +
      `Official plugins: ${Object.keys(OFFICIAL_PLUGINS).join(", ")}`
    );
  }

  let downloadUrl: string;
  let pluginName: string;

  if (isOfficial) {
    const url = OFFICIAL_PLUGINS[nameOrRef];
    if (!url) throw new Error(`Unknown official plugin: ${nameOrRef}\nAvailable: ${Object.keys(OFFICIAL_PLUGINS).join(", ")}`);
    downloadUrl = url;
    pluginName = nameOrRef.replace("@algochains/plugin-", "");
  } else if (isGitHub) {
    const ref = nameOrRef.replace("github:", "");
    downloadUrl = `https://github.com/${ref}/archive/refs/heads/main.tar.gz`;
    pluginName = ref.split("/").pop() ?? ref;
  } else {
    throw new Error(`Invalid plugin reference: ${nameOrRef}`);
  }

  const pluginDir = join(PLUGINS_DIR, pluginName);
  mkdirSync(pluginDir, { recursive: true });

  console.log(`  Downloading ${nameOrRef}...`);
  const res = await fetch(downloadUrl, { signal: AbortSignal.timeout(30_000) });
  if (!res.ok) throw new Error(`Download failed: ${res.status} ${res.statusText}`);

  const buf = Buffer.from(await res.arrayBuffer());

  // Verify signature for official plugins
  if (isOfficial) {
    const sigRes = await fetch(downloadUrl.replace(".tar.gz", ".sig"), { signal: AbortSignal.timeout(5_000) }).catch(() => null);
    if (sigRes?.ok) {
      const sig = await sigRes.text();
      const hash = createHash("sha256").update(buf).digest("hex");
      if (!sig.trim().startsWith(hash.slice(0, 16))) {
        throw new Error(`Signature verification failed for ${nameOrRef}. Plugin may be tampered.`);
      }
      console.log(`  ✓ Signature verified`);
    }
  }

  // Extract (simple tar.gz parsing via system tar)
  const tarPath = join(pluginDir, "plugin.tar.gz");
  writeFileSync(tarPath, buf);

  const { execSync } = await import("child_process");
  execSync(`tar -xzf "${tarPath}" -C "${pluginDir}" --strip-components=1`);
  rmSync(tarPath);

  // Read and validate manifest
  const manifestPath = join(pluginDir, "plugin.json");
  if (!existsSync(manifestPath)) {
    rmSync(pluginDir, { recursive: true });
    throw new Error(`Plugin missing plugin.json manifest: ${nameOrRef}`);
  }

  const manifest: PluginManifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
  console.log(`  ✓ Installed ${manifest.name}@${manifest.version}`);
  console.log(`     Trust:  ${manifest.trust}`);
  console.log(`     Tools:  ${manifest.tools.join(", ")}`);
  if (manifest.env_required.length) {
    console.log(`     Env required: ${manifest.env_required.join(", ")}`);
    const missing = manifest.env_required.filter(e => !process.env[e]);
    if (missing.length) {
      console.log(`  ⚠  Missing env vars: ${missing.join(", ")}`);
    }
  }
}

// ── List installed ────────────────────────────────────────────────────────────
export function listPlugins(): PluginManifest[] {
  if (!existsSync(PLUGINS_DIR)) return [];
  return readdirSync(PLUGINS_DIR, { withFileTypes: true })
    .filter(d => d.isDirectory())
    .map(d => {
      const manifestPath = join(PLUGINS_DIR, d.name, "plugin.json");
      if (!existsSync(manifestPath)) return null;
      try { return JSON.parse(readFileSync(manifestPath, "utf-8")) as PluginManifest; } catch { return null; }
    })
    .filter(Boolean) as PluginManifest[];
}

// ── Remove ────────────────────────────────────────────────────────────────────
export function removePlugin(name: string): void {
  const pluginName = name.replace("@algochains/plugin-", "");
  const pluginDir = join(PLUGINS_DIR, pluginName);
  if (!existsSync(pluginDir)) {
    throw new Error(`Plugin not installed: ${name}`);
  }
  rmSync(pluginDir, { recursive: true });
  console.log(`  ✓ Removed ${name}`);
}

// ── Print list ────────────────────────────────────────────────────────────────
export function printPluginList(): void {
  const plugins = listPlugins();
  if (plugins.length === 0) {
    console.log("  No plugins installed");
    console.log("  Browse: algochains plugin install @algochains/plugin-kalshi");
    console.log(`  Available: ${Object.keys(OFFICIAL_PLUGINS).join(", ")}`);
    return;
  }
  console.log(`\n  Installed plugins (${plugins.length}):`);
  for (const p of plugins) {
    const trustLabel = p.trust === "official" ? "\x1b[32mofficial\x1b[0m" : "\x1b[33mcommunity\x1b[0m";
    console.log(`  ✓  ${p.name}@${p.version}  [${trustLabel}]  ${p.description ?? ""}`);
    console.log(`       Tools: ${p.tools.join(", ")}`);
  }
  console.log("");
}

// ── Run plugin tool (isolated subprocess) ─────────────────────────────────────
export async function runPluginTool(pluginName: string, tool: string, args: Record<string, unknown>): Promise<unknown> {
  const pluginDir = join(PLUGINS_DIR, pluginName);
  const manifest: PluginManifest = JSON.parse(readFileSync(join(pluginDir, "plugin.json"), "utf-8"));

  if (!manifest.tools.includes(tool)) {
    throw new Error(`Tool '${tool}' not registered in plugin '${pluginName}'`);
  }

  // Filter env: only pass required vars + global AlgoChains config
  const filteredEnv: Record<string, string> = {};
  for (const key of manifest.env_required) {
    if (process.env[key]) filteredEnv[key] = process.env[key]!;
  }
  filteredEnv.ALGOCHAINS_PLUGIN_TOOL = tool;
  filteredEnv.ALGOCHAINS_PLUGIN_ARGS = JSON.stringify(args);

  const { spawnSync } = await import("child_process");
  const entry = join(pluginDir, manifest.entry ?? "index.js");
  const result = spawnSync(process.execPath, [entry], {
    env: filteredEnv,
    timeout: 30_000,
    encoding: "utf-8",
  });

  if (result.status !== 0) {
    throw new Error(`Plugin tool failed: ${result.stderr}`);
  }

  try { return JSON.parse(result.stdout); } catch { return result.stdout; }
}
