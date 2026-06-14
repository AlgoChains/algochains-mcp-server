# AlgoChains Developer Onboarding — Programmatic Experience Megaprompt

**How to use this file:** Paste the prompt in the section below into Claude, Cursor,
or any MCP-connected AI assistant. The assistant will call the appropriate MCP tools
at each step and guide you through the complete programmatic developer setup.

---

## Prerequisites

Before running the prompt below:

1. Install the MCP server: `pip install algochains-mcp-server`
2. Connect to your IDE (Cursor, Claude Desktop, Windsurf):
   ```bash
   python scripts/quickstart.py --generate-config cursor
   ```
3. Restart your IDE so the MCP tools are available.
4. Have an email address ready for your AlgoChains account.
5. Have an authenticator app ready (Google Authenticator, Authy, 1Password, etc.).

See [AGENTS.md](AGENTS.md) for detailed IDE connection instructions.

---

## Full Onboarding Prompt (paste into your AI assistant)

```
You are helping me set up a programmatic AlgoChains developer account from scratch.
Follow these steps in order, calling the appropriate MCP tools at each step.
Do NOT skip steps. Confirm each step's result before proceeding.
If a step fails, diagnose and fix it before moving on.

---

STEP 1 — Create account
  Call: signup_algochains(email="<MY_EMAIL>", password="<MY_PASSWORD>")

  Expected responses:
    status: "ok"             → Account created, session active. Proceed to Step 2.
    status: "requires_email_confirm" → Check inbox for confirmation email, then
                               call: verify_email_otp(email="<MY_EMAIL>", token="<TOKEN>")
    error: "already exists"  → Account exists. Go to Step 3 (Login).

---

STEP 2 — Verify email (if required by Step 1)
  After receiving the confirmation email:
  Call: verify_email_otp(email="<MY_EMAIL>", token="<6-DIGIT-OR-LINK-TOKEN>")

  Expected: status: "ok" → Proceed to Step 3.

---

STEP 3 — Login
  Call: login_algochains(email="<MY_EMAIL>", password="<MY_PASSWORD>")

  Expected: status: "ok" with aal: "aal1"
  Note the aal level. If it shows "aal2" you already have MFA — skip to Step 5.

---

STEP 4 — Enroll 2FA (TOTP authenticator — REQUIRED before creating keys)
  Call: enroll_mfa(factor_type="totp")

  Expected: enrollment_started with qr_code_uri and factor_id.

  IMPORTANT: Show me the qr_code_uri string. I will scan it with my authenticator app.
  Tell me to press Enter when I've scanned it so we can continue.

  After I confirm I've scanned the QR code:
  Call: verify_mfa(factor_id="<FACTOR_ID_FROM_ABOVE>", code="<6-DIGIT-TOTP-CODE>")

  Expected: status: "ok", aal: "aal2"
  This upgrades the session to AAL2 — required for key operations.

---

STEP 5 — Create developer API key
  Call: create_developer_key(
    name="primary-dev-key",
    scopes=["read:market_data", "read:signals", "read:regime", "read:macro"],
    env="live"
  )

  CRITICAL: The response will contain a "key" field with the plaintext ac_live_* value.
  Display it clearly and tell me to save it to a password manager IMMEDIATELY.
  The key will NOT be shown again.

  After I confirm I've saved it:
  Tell me to set: export AC_DEV_KEY=<the key value>

---

STEP 6 — Test bridge connection
  Call: test_bridge_connection(api_key="<KEY FROM STEP 5>")

  Expected: status: "ok", bridge: "api.algochains.ai" or similar.
  If it fails: check that the key starts with "ac_live_" and has not been revoked.

---

STEP 7 — Configure local onboarding (for stdio/local use)
  Call: start_onboarding()
  Call: acknowledge_risk_disclosure(text="I have read and understand the risk disclosure above. I accept full responsibility for my trading decisions.")
  Call: set_algochains_api_key(api_key="<KEY FROM STEP 5>")

  Expected: Each step returns status: "ok".
  set_algochains_api_key will confirm the key against the bridge.

---

STEP 8 — Configure guardrail notifications
  Call: set_guardrail_preferences(
    notify_on_daily_loss_pct=80,
    pause_on_consecutive_losses=3,
    slack_alerts_enabled=false
  )

  Note: Hard limits ($500/day loss, 15% max drawdown, VIX>35 gate) cannot be changed.

---

STEP 9 — Generate IDE config
  Call: generate_ide_config(ide="cursor", tool_mode="smart")

  This creates ~/.cursor/mcp.json with your connection settings.
  Restart Cursor after this step.

---

STEP 10 — Run first live queries
  Call: get_quote(symbol="MNQ")
  Call: detect_market_regime()
  Call: get_macro_signals()

  These should return REAL data (not stubs). If you see demo_mode_stub responses,
  check that ALGOCHAINS_DEMO_MODE is not set to 1.

---

STEP 11 — View onboarding status
  Call: get_onboarding_status()

  Expected: progress_pct close to 100%, steps_done includes all completed steps.

---

STEP 12 — Key rotation reminder
  After 90 days, rotate your key:
  Call: list_developer_keys()   ← find your key_id
  Call: rotate_developer_key(key_id="<KEY_ID>", name="primary-dev-key-rotated")
  Save the new key. Set export AC_DEV_KEY=<new key>.
  Optionally revoke the old key: revoke_developer_key(key_id="<OLD_KEY_ID>")

---

POST-ONBOARDING SECURITY CHECKLIST:
  [ ] MFA enrolled — verify: list_mfa_factors() shows ≥1 TOTP factor
  [ ] API key saved in password manager, never in code or .env committed to git
  [ ] Bridge tested successfully (Step 6 passed)
  [ ] Key rotation reminder set for 90 days
  [ ] .gitignore verified: .env, tradovate_session.json, state/ excluded

TROUBLESHOOTING:
  → create_developer_key returns "requires_mfa_challenge":
      Call: list_mfa_factors() to get factor_id
      Call: challenge_mfa(factor_id="<id>")
      Call: verify_mfa(factor_id="<id>", code="<totp>", challenge_id="<challenge_id>")

  → Session expired mid-flow:
      Call: refresh_session()
      If refresh fails: call login_algochains() again, then verify_mfa() to restore AAL2.

  → test_bridge_connection fails with 401:
      Key may be for wrong env (live vs test). Check key prefix.
      Or key was revoked: create_developer_key() for a new one.

  → enroll_mfa fails with "not logged in":
      Session expired. Re-login: login_algochains() first.

That's it! You now have a fully programmatic AlgoChains developer setup.
Please confirm each completed step and report any errors so I can help diagnose.
```

---

## Quick Reference: Key Commands

| Goal | MCP Tool | CLI Alternative |
|------|----------|----------------|
| Create account | `signup_algochains` | `algochains account signup` |
| Login | `login_algochains` | `algochains account login` |
| Enroll 2FA | `enroll_mfa` | `algochains auth mfa enroll` |
| Verify 2FA | `verify_mfa` | `algochains auth mfa verify <code> --factor-id <id>` |
| Create key | `create_developer_key` | `algochains keys create` |
| Test key | `test_bridge_connection` | `algochains keys test` |
| List keys | `list_developer_keys` | `algochains keys list` |
| Rotate key | `rotate_developer_key` | `algochains keys rotate <key-id>` |
| Status | `get_onboarding_status` | `algochains account status` |

---

## SDK (Node.js / TypeScript) Example

```typescript
import { createAlgoChainsClient, createBridgeClient } from "@algochains/sdk";

// Programmatic bridge access (no stdio MCP needed)
const bridge = createBridgeClient({ apiKey: process.env.AC_DEV_KEY });

// Test connection
const health = await bridge.health();
console.log(health.data); // { auth_mode: "developer", scopes: [...] }

// Call any developer-tier tool
const regime = await bridge.call("detect_market_regime", {});
const quote   = await bridge.call("get_quote", { symbol: "MNQ" });
```

---

## CI/CD Service Account Pattern

For automated pipelines (GitHub Actions, etc.), create a dedicated `ac_test_` key
per environment and store it as a repo secret:

```yaml
# .github/workflows/my_workflow.yml
env:
  AC_DEV_KEY: ${{ secrets.AC_DEV_KEY_TEST }}
  ALGOCHAINS_BRIDGE_URL: https://api.algochains.ai

steps:
  - name: Test bridge connectivity
    run: algochains keys test --key $AC_DEV_KEY --json

  - name: Run strategy validation
    run: |
      # Use bridge client in your script
      node -e "
        const { createBridgeClient } = require('@algochains/sdk');
        const b = createBridgeClient();
        b.call('validate_strategy', { strategy_config: { ... } })
          .then(r => console.log(JSON.stringify(r.data, null, 2)));
      "
```

**Key rotation in CI:**
- Create a `ac_test_ci` key for CI (separate from your personal dev key)
- Rotate every 90 days using `algochains keys rotate <id>` and update the secret
- Never commit the key to source control

---

## Security Checklist

After onboarding is complete, verify:

- [ ] `list_mfa_factors()` shows at least 1 TOTP factor
- [ ] `list_developer_keys()` shows your key as active (revoked_at: null)
- [ ] `test_bridge_connection()` returns `status: "ok"`
- [ ] `get_onboarding_status()` shows `algochains_key_set: true`
- [ ] `.gitignore` contains: `.env`, `tradovate_session.json`, `state/`
- [ ] AC_DEV_KEY is in password manager, not hardcoded anywhere
- [ ] 90-day key rotation reminder is set

---

## Related Files

- [AGENTS.md](AGENTS.md) — MCP server setup, transport options, safety rules
- [docs/DEVELOPER_TIER_ONBOARDING.md](docs/DEVELOPER_TIER_ONBOARDING.md) — Bridge tool surface, rate limits, scopes
- [docs/CLI_GAP_ANALYSIS.md](docs/CLI_GAP_ANALYSIS.md) — CLI subcommand roadmap
- [SAFETY_MODEL.md](SAFETY_MODEL.md) — Safety limits and guardrail explanations
- [src/algochains_mcp/auth/platform_auth.py](src/algochains_mcp/auth/platform_auth.py) — MCP tool implementations
- [packages/sdk/src/types.d.ts](packages/sdk/src/types.d.ts) — TypeScript type definitions
