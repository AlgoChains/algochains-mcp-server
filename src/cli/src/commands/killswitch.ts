/**
 * algochains killswitch — Emergency stop for all T2/T3 operations
 *
 * Commands:
 *   algochains killswitch on [--reason "text"]
 *   algochains killswitch off
 *   algochains killswitch status
 *
 * When the KILLSWITCH file exists, all T2/T3 tools are blocked regardless
 * of --confirm or profile. This is a hard stop.
 */
import { enableKillSwitch, isKillSwitchActive, readKillSwitchState } from "../trust.js";
import { KILLSWITCH_FILE } from "../config.js";
import { unlinkSync } from "fs";

const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const YELLOW = "\x1b[33m";
const RESET = "\x1b[0m";
const BOLD = "\x1b[1m";

export function killswitchOn(reason?: string): void {
  enableKillSwitch(reason ?? "manual activation");
  console.log(`\n  ${RED}${BOLD}🛑 KILL SWITCH ACTIVATED${RESET}`);
  console.log(`  All T2/T3 (paper + live) operations are now BLOCKED`);
  if (reason) console.log(`  Reason: ${reason}`);
  console.log(`  File:   ${KILLSWITCH_FILE}`);
  console.log(`  Resume: algochains killswitch off\n`);
}

export function killswitchOff(): void {
  if (!isKillSwitchActive()) {
    console.log(`  Kill switch is not active`);
    return;
  }
  try {
    unlinkSync(KILLSWITCH_FILE);
    console.log(`\n  ${GREEN}✓ Kill switch deactivated — trading operations allowed${RESET}\n`);
  } catch (e) {
    console.error(`  Failed to remove kill switch file: ${e}`);
    process.exit(1);
  }
}

export function killswitchStatus(): void {
  const state = readKillSwitchState();
  if (state.active) {
    console.log(`\n  ${RED}${BOLD}🛑 KILL SWITCH: ACTIVE${RESET}`);
    if (state.activated_at) console.log(`  Activated: ${state.activated_at}`);
    if (state.reason) console.log(`  Reason:    ${state.reason}`);
    console.log(`  File:      ${KILLSWITCH_FILE}`);
    console.log(`  Effect:    All T2/T3 (place-order, flatten, restart-bot) BLOCKED`);
    console.log(`  Resume:    ${YELLOW}algochains killswitch off${RESET}\n`);
  } else {
    console.log(`\n  ${GREEN}✓ Kill switch: INACTIVE${RESET}`);
    console.log(`  Trading operations (T2/T3) are allowed`);
    console.log(`  Emergency stop: algochains killswitch on\n`);
  }
}
