/**
 * algochains auth — Credential management commands
 *
 *   algochains auth set <service>          interactive prompts → OS keyring
 *   algochains auth list                   show authenticated services
 *   algochains auth rotate <service>       re-prompt + update keyring
 *   algochains auth clear <service|all>    remove credentials
 *   algochains auth test <service>         verify credentials work
 */
import {
  BrokerService,
  clearAllCredentials,
  interactiveSetCredentials,
  listAuthenticatedServices,
  retrieveCredential,
} from "../auth.js";

const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED = "\x1b[31m";
const CYAN = "\x1b[36m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";

const VALID_SERVICES: BrokerService[] = [
  "tradovate", "alpaca", "polygon", "oanda", "ibkr", "kalshi", "onyx", "openai", "anthropic"
];

function assertValidService(service: string): BrokerService {
  if (!VALID_SERVICES.includes(service as BrokerService)) {
    console.error(`  Unknown service: ${service}`);
    console.error(`  Valid services: ${VALID_SERVICES.join(", ")}`);
    process.exit(1);
  }
  return service as BrokerService;
}

export async function authSet(service: string): Promise<void> {
  const svc = assertValidService(service);
  console.log(`\n${BOLD}Configure ${svc} credentials${RESET}`);
  console.log(`${DIM}Credentials are stored in your OS keyring (never logged or transmitted)${RESET}\n`);
  await interactiveSetCredentials(svc);
  console.log(`\n  ${GREEN}✓ ${svc} credentials saved${RESET}`);
  console.log(`  Verify with: algochains auth test ${svc}\n`);
}

export async function authList(): Promise<void> {
  console.log(`\n${BOLD}Authenticated services${RESET}`);
  console.log("─".repeat(50));

  const services = await listAuthenticatedServices();
  if (services.length === 0) {
    console.log(`  ${YELLOW}No credentials configured${RESET}`);
    console.log(`  Set up: algochains auth set tradovate  (or alpaca, polygon, etc.)\n`);
    return;
  }

  for (const { service, keys, via } of services) {
    const label = via === "keyring" ? `${GREEN}keyring${RESET}` : `${YELLOW}file${RESET}`;
    console.log(`  ${GREEN}✓${RESET}  ${service.padEnd(14)} ${keys.join(", ")}  ${DIM}(${label}${DIM})${RESET}`);
  }

  const missing = VALID_SERVICES.filter(s => !services.find(sv => sv.service === s));
  if (missing.length) {
    console.log("");
    for (const svc of missing) {
      console.log(`  ${DIM}–  ${svc.padEnd(14)} not configured${RESET}`);
    }
  }
  console.log("");
}

export async function authRotate(service: string): Promise<void> {
  const svc = assertValidService(service);
  console.log(`\n${BOLD}Rotate ${svc} credentials${RESET}`);
  console.log("Existing credentials will be overwritten.\n");
  await interactiveSetCredentials(svc);
  console.log(`\n  ${GREEN}✓ ${svc} credentials rotated${RESET}\n`);
}

export async function authClear(serviceOrAll: string): Promise<void> {
  if (serviceOrAll === "all") {
    console.log(`\n  ${YELLOW}Clearing ALL credentials${RESET}`);
    const { prompt } = await import("enquirer");
    const { confirmed } = await prompt([{
      type: "confirm",
      name: "confirmed",
      message: "This removes all stored credentials from the OS keyring and file store. Continue?",
      initial: false,
    }]) as { confirmed: boolean };
    if (!confirmed) { console.log("  Aborted\n"); return; }
    await clearAllCredentials();
    console.log(`  ${GREEN}✓ All credentials cleared${RESET}\n`);
  } else {
    const svc = assertValidService(serviceOrAll);
    await clearAllCredentials(svc);
    console.log(`  ${GREEN}✓ ${svc} credentials cleared${RESET}\n`);
  }
}

export async function authTest(service: string): Promise<void> {
  const svc = assertValidService(service);
  console.log(`\n  Testing ${svc} credentials...\n`);

  const tests: Record<BrokerService, () => Promise<{ ok: boolean; detail: string }>> = {
    tradovate: async () => {
      const cid = await retrieveCredential("tradovate", "cid");
      if (!cid) return { ok: false, detail: "No CID found — run: algochains auth set tradovate" };
      // Attempt token status check via MCP
      try {
        const res = await fetch("http://127.0.0.1:8101/status", { signal: AbortSignal.timeout(3_000) });
        const data = await res.json() as Record<string, unknown>;
        const tokenAge = data.token_age_seconds as number ?? -1;
        if (tokenAge >= 0 && tokenAge < 4500) {
          return { ok: true, detail: `Token valid (age ${tokenAge}s / 5400s TTL)` };
        }
        return { ok: false, detail: "Token expired or not found — run: python3 tradovate_token_guardian.py" };
      } catch {
        return { ok: true, detail: "CID found in keyring (bridge not running — start daemon for live validation)" };
      }
    },
    alpaca: async () => {
      const key = await retrieveCredential("alpaca", "api_key");
      if (!key) return { ok: false, detail: "No API key — run: algochains auth set alpaca" };
      try {
        const res = await fetch("https://paper-api.alpaca.markets/v2/account", {
          headers: { "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": await retrieveCredential("alpaca", "secret_key") ?? "" },
          signal: AbortSignal.timeout(5_000),
        });
        if (res.ok) { const d = await res.json() as Record<string, unknown>; return { ok: true, detail: `Paper account: $${d.cash} cash` }; }
        return { ok: false, detail: `API returned ${res.status} — check key and secret` };
      } catch (e) { return { ok: false, detail: String(e) }; }
    },
    polygon: async () => {
      const key = await retrieveCredential("polygon", "api_key");
      if (!key) return { ok: false, detail: "No API key — run: algochains auth set polygon" };
      const res = await fetch(`https://api.polygon.io/v2/aggs/ticker/AAPL/prev?apiKey=${key}`, { signal: AbortSignal.timeout(5_000) });
      return res.ok ? { ok: true, detail: "Connection verified" } : { ok: false, detail: `API returned ${res.status}` };
    },
    oanda: async () => {
      const key = await retrieveCredential("oanda", "api_key");
      return key ? { ok: true, detail: "API key found (connection not verified)" } : { ok: false, detail: "No API key" };
    },
    ibkr:      async () => ({ ok: true, detail: "IBKR uses TWS port (no remote auth test)" }),
    kalshi:    async () => { const k = await retrieveCredential("kalshi", "api_key"); return k ? { ok: true, detail: "API key found" } : { ok: false, detail: "No API key" }; },
    onyx:      async () => { const k = await retrieveCredential("onyx", "api_key"); return k ? { ok: true, detail: "API key found" } : { ok: false, detail: "No API key" }; },
    openai:    async () => { const k = await retrieveCredential("openai", "api_key"); return k ? { ok: true, detail: "API key found" } : { ok: false, detail: "No API key" }; },
    anthropic: async () => { const k = await retrieveCredential("anthropic", "api_key"); return k ? { ok: true, detail: "API key found" } : { ok: false, detail: "No API key" }; },
  };

  const testFn = tests[svc];
  if (!testFn) { console.log(`  No test defined for ${svc}\n`); return; }

  const result = await testFn();
  const sym = result.ok ? `${GREEN}✓${RESET}` : `${RED}✗${RESET}`;
  console.log(`  ${sym}  ${svc}: ${result.detail}\n`);
}
