/**
 * AlgoChains CLI — Credential Management
 *
 * Storage hierarchy:
 *   1. OS keyring (macOS Keychain, Linux SecretService, Windows Credential Manager)
 *   2. ~/.algochains/credentials.toml (mode 0600) as fallback
 *   3. Environment variables (lowest trust, read-only from auth module)
 *
 * NEVER logs secret values. Only logs service names and whether auth succeeded.
 */
import { existsSync, readFileSync, writeFileSync } from "fs";
import { execSync } from "child_process";
import { CREDENTIALS_FILE } from "./config.js";

export type BrokerService = "tradovate" | "alpaca" | "polygon" | "oanda" | "ibkr" | "kalshi" | "onyx" | "openai" | "anthropic";

export interface TradovateCredentials {
  cid: string;
  secret: string;
  device_id?: string;
  env: "live" | "demo";
}

export interface AlpacaCredentials {
  api_key: string;
  secret_key: string;
  paper: boolean;
}

export interface SimpleApiKey {
  api_key: string;
}

type Credentials = TradovateCredentials | AlpacaCredentials | SimpleApiKey;

const KEYRING_SERVICE = "algochains";

// ── Keyring: try native, fall back to file ────────────────────────────────────
async function keytarSet(account: string, value: string): Promise<boolean> {
  try {
    const keytar = await import("keytar").catch(() => null);
    if (keytar) {
      await keytar.setPassword(KEYRING_SERVICE, account, value);
      return true;
    }
  } catch { /* fall through */ }
  return false;
}

async function keytarGet(account: string): Promise<string | null> {
  try {
    const keytar = await import("keytar").catch(() => null);
    if (keytar) return await keytar.getPassword(KEYRING_SERVICE, account);
  } catch { /* fall through */ }
  return null;
}

async function keytarDelete(account: string): Promise<boolean> {
  try {
    const keytar = await import("keytar").catch(() => null);
    if (keytar) return await keytar.deletePassword(KEYRING_SERVICE, account);
  } catch { /* fall through */ }
  return false;
}

// ── File credential store (fallback) ─────────────────────────────────────────
function readCredFile(): Record<string, Record<string, string>> {
  if (!existsSync(CREDENTIALS_FILE)) return {};
  try {
    const TOML = require("toml") as typeof import("toml");
    return (TOML.parse(readFileSync(CREDENTIALS_FILE, "utf-8")) as Record<string, Record<string, string>>) ?? {};
  } catch { return {}; }
}

function writeCredFile(data: Record<string, Record<string, string>>): void {
  const lines: string[] = ["# AlgoChains CLI Credentials (fallback store)", "# Prefer OS keyring. This file is 0600.", ""];
  for (const [service, fields] of Object.entries(data)) {
    lines.push(`[${service}]`);
    for (const [k, v] of Object.entries(fields)) {
      lines.push(`${k} = ${JSON.stringify(v)}`);
    }
    lines.push("");
  }
  writeFileSync(CREDENTIALS_FILE, lines.join("\n"), { mode: 0o600 });
}

// ── Public API ────────────────────────────────────────────────────────────────
export async function storeCredential(service: BrokerService, key: string, value: string): Promise<"keyring" | "file"> {
  const account = `${service}:${key}`;
  const stored = await keytarSet(account, value);
  if (stored) return "keyring";

  // Fallback to file
  const data = readCredFile();
  if (!data[service]) data[service] = {};
  data[service][key] = value;
  writeCredFile(data);
  return "file";
}

export async function retrieveCredential(service: BrokerService, key: string): Promise<string | null> {
  // 1. OS keyring
  const fromKeyring = await keytarGet(`${service}:${key}`);
  if (fromKeyring) return fromKeyring;

  // 2. File fallback
  const data = readCredFile();
  if (data[service]?.[key]) return data[service][key];

  // 3. Environment variable (e.g. TRADOVATE_CID, ALPACA_API_KEY)
  const envKey = `${service.toUpperCase()}_${key.toUpperCase()}`;
  return process.env[envKey] ?? null;
}

export async function deleteCredential(service: BrokerService, key: string): Promise<void> {
  await keytarDelete(`${service}:${key}`);
  const data = readCredFile();
  if (data[service]) {
    delete data[service][key];
    if (Object.keys(data[service]).length === 0) delete data[service];
    writeCredFile(data);
  }
}

export async function clearAllCredentials(service?: BrokerService): Promise<void> {
  const data = readCredFile();
  if (service) {
    delete data[service];
    writeCredFile(data);
    // Try to remove from keyring too (best-effort)
    const fields = ["api_key", "secret_key", "cid", "secret", "device_id"];
    for (const f of fields) await keytarDelete(`${service}:${f}`).catch(() => {});
  } else {
    // Clear all
    writeCredFile({});
    // Note: can't enumerate keyring entries without listing — leave them (they're scoped to service name)
  }
}

export async function listAuthenticatedServices(): Promise<Array<{ service: string; keys: string[]; via: string }>> {
  const services: BrokerService[] = ["tradovate", "alpaca", "polygon", "oanda", "ibkr", "kalshi", "onyx", "openai", "anthropic"];
  const result = [];
  const fileData = readCredFile();

  for (const svc of services) {
    const keys: string[] = [];
    // Check keyring
    const keyFields: Record<BrokerService, string[]> = {
      tradovate: ["cid", "secret", "device_id"],
      alpaca: ["api_key", "secret_key"],
      polygon: ["api_key"],
      oanda: ["api_key", "account_id"],
      ibkr: ["client_id", "port"],
      kalshi: ["api_key"],
      onyx: ["api_key"],
      openai: ["api_key"],
      anthropic: ["api_key"],
    };

    for (const field of keyFields[svc] ?? []) {
      const val = await keytarGet(`${svc}:${field}`);
      if (val || fileData[svc]?.[field]) keys.push(field);
    }

    if (keys.length > 0) {
      const via = await keytarGet(`${svc}:${keys[0]}`) ? "keyring" : "file";
      result.push({ service: svc, keys, via });
    }
  }
  return result;
}

/** Interactive credential setup using enquirer prompts */
export async function interactiveSetCredentials(service: BrokerService): Promise<void> {
  const { prompt } = await import("enquirer");

  const questions: Record<BrokerService, Array<{ name: string; message: string; type?: string }>> = {
    tradovate: [
      { name: "cid",       message: "Client ID (CID)",           type: "input" },
      { name: "secret",    message: "Client Secret",              type: "password" },
      { name: "device_id", message: "Device ID (optional)",       type: "input" },
    ],
    alpaca: [
      { name: "api_key",    message: "Alpaca API Key",            type: "input" },
      { name: "secret_key", message: "Alpaca Secret Key",         type: "password" },
    ],
    polygon: [{ name: "api_key", message: "Polygon API Key",      type: "password" }],
    oanda:   [{ name: "api_key", message: "OANDA API Key",         type: "password" },
              { name: "account_id", message: "Account ID",         type: "input" }],
    ibkr:    [{ name: "port",    message: "TWS/IB Gateway Port",  type: "input" }],
    kalshi:  [{ name: "api_key", message: "Kalshi API Key",        type: "password" }],
    onyx:    [{ name: "api_key", message: "Onyx API Key",          type: "password" }],
    openai:  [{ name: "api_key", message: "OpenAI API Key",        type: "password" }],
    anthropic: [{ name: "api_key", message: "Anthropic API Key",  type: "password" }],
  };

  const qs = questions[service];
  if (!qs) throw new Error(`Unknown service: ${service}`);

  const answers = await prompt(qs.map(q => ({ ...q, type: q.type ?? "input" }))) as Record<string, string>;

  for (const [key, value] of Object.entries(answers)) {
    if (!value) continue;
    const via = await storeCredential(service, key, value);
    console.log(`  ✓ ${key} stored in ${via}`);
  }
}
