# CI/CD Service Account Keys — Developer Guide

This guide covers how to set up AlgoChains developer API keys for automated
pipelines (GitHub Actions, GitLab CI, CircleCI, etc.) and integrate with cloud
secrets managers (AWS Secrets Manager, HashiCorp Vault, 1Password, etc.).

---

## Overview

CI/CD pipelines should use **dedicated service account keys** (separate from your
personal developer keys) with the **minimum required scopes**. This limits blast
radius if a key is leaked.

| Key type | Use | Scopes |
|----------|-----|--------|
| `ac_live_*` personal | Local development | Wide read scopes |
| `ac_test_*` CI | Automated testing | `read:market_data`, `read:regime` only |
| `ac_live_*` CD | Production deployments | Narrow, per-pipeline |

---

## Step 1: Create a service account key

Service account keys should be created with MFA (AAL2 session) using the MCP tool
or CLI, not the web portal, so the process is scriptable in your ops runbooks.

### Via CLI (interactive, one-time setup)

```bash
# Login (requires SUPABASE_URL + SUPABASE_ANON_KEY in .env)
algochains account login --email your@algochains.io

# Verify MFA (get factor_id from: algochains auth mfa list)
algochains auth mfa verify <totp-code> --factor-id <factor-id>

# Create CI key (test environment, minimal scopes)
algochains keys create \
  --name "github-actions-ci" \
  --scopes read:market_data read:regime \
  --env test \
  --json

# Output: { "key": "ac_test_...", "key_id": "...", ... }
# Save the key immediately — it's shown once only.
```

### Via MCP tool

```python
# In an MCP-connected session with AAL2:
create_developer_key(
    name="github-actions-ci",
    scopes=["read:market_data", "read:regime"],
    env="test"
)
```

---

## Step 2: Store the key in your secrets manager

### GitHub Actions

```bash
# Using GitHub CLI
gh secret set AC_DEV_KEY --body "ac_test_YOUR_KEY_HERE" --repo owner/repo
```

```yaml
# .github/workflows/strategy_ci.yml
name: Strategy CI

on: [push, pull_request]

env:
  AC_DEV_KEY: ${{ secrets.AC_DEV_KEY }}
  ALGOCHAINS_BRIDGE_URL: https://mcp.algochains.ai

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install AlgoChains MCP
        run: pip install algochains-mcp-server

      - name: Test bridge connectivity
        run: algochains keys test --key "$AC_DEV_KEY" --json

      - name: Validate strategy
        run: |
          python3 - << 'EOF'
          import asyncio, os, json
          from algochains_mcp.auth.platform_auth import test_bridge_connection

          async def main():
              result = await test_bridge_connection(api_key=os.environ["AC_DEV_KEY"])
              if result.get("status") != "ok":
                  raise SystemExit(f"Bridge auth failed: {result}")
              print("✅ Bridge connected:", json.dumps(result, indent=2))

          asyncio.run(main())
          EOF
```

### GitLab CI/CD

```yaml
# .gitlab-ci.yml
variables:
  AC_DEV_KEY: $AC_DEV_KEY  # Set in GitLab > Settings > CI/CD > Variables

test_strategy:
  image: python:3.12
  script:
    - pip install algochains-mcp-server
    - algochains keys test --key "$AC_DEV_KEY" --json
```

### AWS Secrets Manager

```bash
# Store key
aws secretsmanager create-secret \
  --name "algochains/ci/ac-dev-key" \
  --description "AlgoChains CI API key" \
  --secret-string "ac_test_YOUR_KEY_HERE"

# Retrieve in pipeline
AC_DEV_KEY=$(aws secretsmanager get-secret-value \
  --secret-id "algochains/ci/ac-dev-key" \
  --query SecretString \
  --output text)
```

```python
# Python: read from Secrets Manager in a Lambda or ECS task
import boto3

def get_algochains_key() -> str:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId="algochains/ci/ac-dev-key")
    return response["SecretString"]
```

### HashiCorp Vault

```bash
# Store
vault kv put secret/algochains/ci ac_dev_key="ac_test_YOUR_KEY_HERE"

# Retrieve
AC_DEV_KEY=$(vault kv get -field=ac_dev_key secret/algochains/ci)
```

### 1Password (Service Account)

```bash
# Using 1Password CLI
op item create \
  --category login \
  --title "AlgoChains CI Key" \
  --vault "CI Secrets" \
  username="ci" \
  password="ac_test_YOUR_KEY_HERE"

# Inject in CI
AC_DEV_KEY=$(op item get "AlgoChains CI Key" --field password)
```

---

## Step 3: Key-per-environment pattern

Maintain separate keys for each environment to isolate blast radius:

| Environment | Key prefix | Scopes | Rotation |
|-------------|-----------|--------|---------|
| Development (local) | `ac_live_` | Wide (dev needs) | 90 days |
| Test (CI) | `ac_test_` | Narrow (read only) | 90 days |
| Staging | `ac_test_` | Read + backtest | 60 days |
| Production (if needed) | `ac_live_` | Specific write scopes | 30 days |

**Never** use your personal developer key in CI/CD — a leaked CI secret would
invalidate your personal key if they're the same.

---

## Step 4: Key rotation schedule

### Manual rotation (CLI)

```bash
# 1. Find the key to rotate
algochains keys list --json | jq '.keys[] | select(.name == "github-actions-ci")'

# 2. Rotate (atomically mints new + revokes old)
algochains keys rotate <KEY_ID> --name "github-actions-ci-rotated" --json
# Output contains new plaintext key — save immediately

# 3. Update your secrets manager
gh secret set AC_DEV_KEY --body "ac_test_NEW_KEY_HERE" --repo owner/repo

# 4. Verify new key works
algochains keys test --key "ac_test_NEW_KEY_HERE" --json
```

### Automated rotation (GitHub Actions, every 90 days)

```yaml
# .github/workflows/key_rotation.yml
name: Rotate AlgoChains CI Key

on:
  schedule:
    - cron: "0 10 1 */3 *"  # Every 90 days (1st day of Jan/Apr/Jul/Oct)
  workflow_dispatch:

jobs:
  rotate:
    runs-on: ubuntu-latest
    steps:
      - name: Rotate via MCP
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_ANON_KEY: ${{ secrets.SUPABASE_ANON_KEY }}
          AC_DEV_KEY: ${{ secrets.AC_DEV_KEY }}
          # MFA session: pre-authenticate offline and store short-lived token
          ALGOCHAINS_ACCESS_TOKEN: ${{ secrets.ALGOCHAINS_ACCESS_TOKEN }}
        run: |
          pip install algochains-mcp-server
          python3 scripts/rotate_ci_key.py
```

```python
# scripts/rotate_ci_key.py
"""
Automated CI key rotation script.
Requires pre-authenticated AAL2 session token in ALGOCHAINS_ACCESS_TOKEN.
"""
import asyncio, json, os, subprocess, sys

async def main():
    from algochains_mcp.auth.platform_auth import (
        list_developer_keys, rotate_developer_key
    )

    # Find CI key
    keys = await list_developer_keys()
    ci_key = next(
        (k for k in keys.get("keys", []) if k["name"] == "github-actions-ci"),
        None
    )
    if not ci_key:
        sys.exit("CI key 'github-actions-ci' not found")

    # Rotate
    result = await rotate_developer_key(key_id=ci_key["id"])
    if result.get("status") != "ok":
        sys.exit(f"Rotation failed: {result}")

    new_key = result["new_key"]
    print(f"✅ Key rotated. New prefix: {new_key[:12]}***")

    # Update GitHub secret (requires GITHUB_TOKEN with secrets:write)
    subprocess.run([
        "gh", "secret", "set", "AC_DEV_KEY",
        "--body", new_key,
        "--repo", os.environ["GITHUB_REPOSITORY"],
    ], check=True)
    print("✅ GitHub secret updated")

asyncio.run(main())
```

---

## Step 5: Bridge client in scripts

Use `createBridgeClient` from the SDK for direct programmatic access without
the stdio MCP transport:

```typescript
// scripts/validate_strategy.ts
import { createBridgeClient } from "@algochains/sdk";

const bridge = createBridgeClient({
  apiKey: process.env.AC_DEV_KEY,
  // baseUrl defaults to https://mcp.algochains.ai
});

async function main() {
  // Test auth
  const health = await bridge.health();
  if (!health.ok) {
    console.error("Bridge auth failed:", health.error);
    process.exit(1);
  }

  // Run validation
  const result = await bridge.call("validate_strategy", {
    strategy_config: {
      symbol: "MNQ",
      timeframe: "5m",
      entry: "momentum",
    },
  });

  if (!result.ok) {
    console.error("Validation failed:", result.error);
    process.exit(1);
  }

  console.log("✅ Strategy valid:", JSON.stringify(result.data, null, 2));
}

main().catch(e => { console.error(e); process.exit(1); });
```

```python
# scripts/validate_strategy.py
import asyncio, os
import httpx

AC_DEV_KEY = os.environ["AC_DEV_KEY"]
BRIDGE_URL = os.environ.get("ALGOCHAINS_BRIDGE_URL", "https://mcp.algochains.ai")

async def call_bridge(tool: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/api/mcp",
            headers={"X-Api-Key": AC_DEV_KEY, "Content-Type": "application/json"},
            json={"tool": tool, "arguments": params},
        )
        resp.raise_for_status()
        return resp.json()

async def main():
    # Test connectivity
    async with httpx.AsyncClient() as client:
        health = await client.get(f"{BRIDGE_URL}/health", headers={"X-Api-Key": AC_DEV_KEY})
        assert health.status_code == 200, f"Bridge auth failed: {health.status_code}"

    # Validate
    result = await call_bridge("validate_strategy", {"strategy_config": {"symbol": "MNQ"}})
    print("✅ Result:", result)

asyncio.run(main())
```

---

## Environment variable reference

| Variable | Purpose | Required for |
|----------|---------|-------------|
| `AC_DEV_KEY` | Developer API key (`ac_live_*` or `ac_test_*`) | Bridge access |
| `ALGOCHAINS_BRIDGE_URL` | Bridge endpoint (default: `https://mcp.algochains.ai`) | Custom deployments |
| `SUPABASE_URL` | Supabase project URL | MCP account/key tools |
| `SUPABASE_ANON_KEY` | Supabase anon key | MCP account/key tools |
| `SUPABASE_SERVICE_KEY` | Supabase service role key | Key creation/rotation |

---

## Security best practices

1. **One key per service/pipeline** — never share keys across different CI systems
2. **Minimum scope** — request only scopes the pipeline actually needs
3. **Rotate every 90 days** — automate rotation (see Step 4 above)
4. **Audit usage** — call `get_developer_key_usage(key_id=...)` to spot anomalies
5. **Never log keys** — mask in CI logs; keys in query params show in access logs
6. **Revoke on compromise** — `algochains keys revoke <key-id>` immediately
7. **Use test keys in CI** — `ac_test_` keys cannot execute live orders
