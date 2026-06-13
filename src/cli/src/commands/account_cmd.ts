/**
 * algochains account — AlgoChains platform account commands
 *
 *   algochains account signup [--email] [--no-mfa]
 *   algochains account login  [--email]
 *   algochains account logout
 *   algochains account status            # shows session expiry, MFA factors
 *   algochains auth mfa enroll [--type totp|sms]
 *   algochains auth mfa verify <code>
 *   algochains auth mfa list
 *   algochains auth mfa remove <factor-id>
 *
 * All operations proxy through the MCP server's platform_auth module which
 * reads SUPABASE_URL + SUPABASE_ANON_KEY from environment.
 */
import readline from "readline";
import { createMcpClient, extractText } from "../mcp_client.js";
import { loadConfig } from "../config.js";

const GREEN  = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED    = "\x1b[31m";
const CYAN   = "\x1b[36m";
const DIM    = "\x1b[2m";
const RESET  = "\x1b[0m";
const BOLD   = "\x1b[1m";

async function prompt(question: string, hidden = false): Promise<string> {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    if (hidden) {
      process.stdout.write(question);
      process.stdin.once("data", (chunk) => {
        process.stdout.write("\n");
        rl.close();
        resolve(chunk.toString().trim());
      });
      // Disable echo
      if ((process.stdin as any).isTTY) (process.stdin as any).setRawMode?.(true);
    } else {
      rl.question(question, (answer) => {
        rl.close();
        resolve(answer.trim());
      });
    }
  });
}

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

export async function accountSignup(opts: { email?: string; noMfa?: boolean; json?: boolean }): Promise<void> {
  console.log(`\n${BOLD}Create AlgoChains Account${RESET}`);
  const email = opts.email || await prompt("Email: ");
  const password = await prompt("Password (min 8 chars): ", true);

  console.log(`\n${DIM}Creating account...${RESET}`);
  await callMcp("signup_algochains", { email, password }, opts.json ?? false);

  if (!opts.noMfa) {
    console.log(`\n${YELLOW}Next:${RESET} Verify your email (check inbox for a confirmation link or OTP),`);
    console.log(`then run: ${CYAN}algochains account login --email ${email}${RESET}`);
    console.log(`then:      ${CYAN}algochains auth mfa enroll${RESET} to secure your account\n`);
  }
}

export async function accountLogin(opts: { email?: string; json?: boolean }): Promise<void> {
  console.log(`\n${BOLD}Login to AlgoChains${RESET}`);
  const email = opts.email || await prompt("Email: ");
  const password = await prompt("Password: ", true);

  console.log(`\n${DIM}Logging in...${RESET}`);
  await callMcp("login_algochains", { email, password }, opts.json ?? false);
  console.log(`\n${DIM}Tip: If you see 'aal1' (no MFA), run: ${CYAN}algochains auth mfa enroll${RESET}${DIM} to protect your account${RESET}\n`);
}

export async function accountLogout(opts: { json?: boolean }): Promise<void> {
  await callMcp("logout_algochains", {}, opts.json ?? false);
}

export async function accountStatus(opts: { json?: boolean }): Promise<void> {
  await callMcp("get_onboarding_status", {}, opts.json ?? false);
}

// MFA subcommands

export async function mfaEnroll(opts: { type?: string; json?: boolean }): Promise<void> {
  const factorType = opts.type ?? "totp";
  console.log(`\n${BOLD}Enroll MFA (${factorType.toUpperCase()})${RESET}`);
  if (factorType === "totp") {
    console.log(`${DIM}You will receive a QR code URI to scan with your authenticator app.${RESET}\n`);
  }
  await callMcp("enroll_mfa", { factor_type: factorType }, opts.json ?? false);
  console.log(`\n${YELLOW}Next:${RESET} After scanning the QR code, run:`);
  console.log(`  ${CYAN}algochains auth mfa verify <6-digit-code>${RESET}\n`);
}

export async function mfaVerify(code: string, opts: { factorId?: string; challengeId?: string; json?: boolean }): Promise<void> {
  if (!opts.factorId) {
    console.error(`${RED}Error:${RESET} --factor-id required. Get it from: algochains auth mfa list`);
    process.exit(1);
  }
  await callMcp("verify_mfa", {
    factor_id: opts.factorId,
    code,
    challenge_id: opts.challengeId,
  }, opts.json ?? false);
}

export async function mfaList(opts: { json?: boolean }): Promise<void> {
  await callMcp("list_mfa_factors", {}, opts.json ?? false);
}

export async function mfaRemove(factorId: string, ownerToken: string, opts: { json?: boolean }): Promise<void> {
  console.log(`\n${YELLOW}⚠️  Removing MFA factor ${factorId}${RESET}`);
  await callMcp("remove_mfa_factor", { factor_id: factorId, owner_token: ownerToken }, opts.json ?? false);
}
