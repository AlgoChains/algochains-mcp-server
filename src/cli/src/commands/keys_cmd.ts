/**
 * algochains keys — Developer API key lifecycle commands
 *
 *   algochains keys create [--name] [--scopes ...] [--env live|test]
 *   algochains keys list   [--json]
 *   algochains keys rotate <key-id>
 *   algochains keys revoke <key-id>
 *   algochains keys test   [--key ac_live_...]
 *
 * Keys are self-serve developer credentials (ac_live_* / ac_test_*) that
 * authenticate against the AlgoChains hosted HTTP bridge at mcp.algochains.ai.
 *
 * Requires an AAL2 session (login + MFA) for create / rotate / revoke.
 * Set AC_DEV_KEY env var or pass --key to test.
 */
import { createMcpClient, extractText } from "../mcp_client.js";
import { loadConfig } from "../config.js";

const GREEN  = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED    = "\x1b[31m";
const CYAN   = "\x1b[36m";
const BOLD   = "\x1b[1m";
const DIM    = "\x1b[2m";
const RESET  = "\x1b[0m";

async function callMcp(tool: string, args: Record<string, unknown>, jsonOutput: boolean): Promise<void> {
  const config = loadConfig();
  const mcp = createMcpClient(config);
  const result = await mcp.callTool(tool, args);
  const text = extractText(result);
  if (jsonOutput) {
    try { console.log(JSON.stringify(JSON.parse(text), null, 2)); return; } catch {}
  }
  console.log(text);
}

export async function keysCreate(opts: {
  name?: string;
  scopes?: string[];
  env?: string;
  json?: boolean;
}): Promise<void> {
  console.log(`\n${BOLD}Create Developer API Key${RESET}`);
  console.log(`${YELLOW}⚠️  Requires AAL2 session. Login and enroll MFA first if not done.${RESET}`);
  console.log(`${DIM}The plaintext key will be shown ONCE. Save it to a password manager.${RESET}\n`);

  await callMcp("create_developer_key", {
    name: opts.name ?? "default",
    scopes: opts.scopes ?? ["read:market_data", "read:signals"],
    env: opts.env ?? "live",
  }, opts.json ?? false);

  if (!opts.json) {
    console.log(`\n${DIM}Next: Test the key with: ${CYAN}algochains keys test${RESET}`);
    console.log(`${DIM}Or set it locally:       ${CYAN}export AC_DEV_KEY=ac_live_...${RESET}\n`);
  }
}

export async function keysList(opts: { json?: boolean }): Promise<void> {
  await callMcp("list_developer_keys", {}, opts.json ?? false);
}

export async function keysRotate(keyId: string, opts: { name?: string; json?: boolean }): Promise<void> {
  console.log(`\n${BOLD}Rotate Key${RESET} ${keyId}`);
  console.log(`${YELLOW}⚠️  Requires AAL2 session (MFA). The old key will be revoked immediately.${RESET}`);
  console.log(`${DIM}New plaintext key shown ONCE — save it immediately.${RESET}\n`);
  await callMcp("rotate_developer_key", { key_id: keyId, name: opts.name }, opts.json ?? false);
}

export async function keysRevoke(keyId: string, opts: { json?: boolean }): Promise<void> {
  console.log(`\n${RED}Revoke Key${RESET} ${keyId}`);
  console.log(`${YELLOW}⚠️  Requires AAL2 session (MFA). This key will stop working immediately.${RESET}\n`);
  await callMcp("revoke_developer_key", { key_id: keyId }, opts.json ?? false);
}

export async function keysTest(opts: { key?: string; json?: boolean }): Promise<void> {
  const apiKey = opts.key ?? process.env.AC_DEV_KEY ?? "";
  if (!apiKey) {
    console.error(`${RED}Error:${RESET} No key provided. Use --key or set AC_DEV_KEY env var.`);
    console.error(`  Create a key: ${CYAN}algochains keys create${RESET}`);
    process.exit(1);
  }

  if (!opts.json) {
    console.log(`\n${BOLD}Testing bridge connection${RESET}`);
    console.log(`${DIM}Key: ${apiKey.substring(0, 12)}***${RESET}`);
    console.log(`${DIM}Bridge: ${process.env.ALGOCHAINS_BRIDGE_URL ?? "https://mcp.algochains.ai"}${RESET}\n`);
  }

  await callMcp("test_bridge_connection", { api_key: apiKey }, opts.json ?? false);
}
