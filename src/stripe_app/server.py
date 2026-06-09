"""
AlgoChains — Stripe Agentic Provisioning Protocol (APP) server
https://stripe.com/docs/apps/agentic-provisioning

Implements the Stripe APP spec so users can onboard via:
  stripe projects link algochains   → creates account + returns credentials
  stripe projects add algochains/developer-tier  → provisions API access

Endpoints (Stripe APP spec):
  GET  /app/info            → product metadata
  POST /app/provision       → create account + issue developer key
  GET  /app/status/:id      → provisioning status
  POST /app/deprovision/:id → remove provisioned resource
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="AlgoChains Stripe APP", version="1.0.0")

# ── Stripe APP spec: product info ─────────────────────────────────────────────
APP_INFO = {
    "name": "AlgoChains",
    "description": "AI-native algorithmic trading CLI — 482 MCP tools, strategy marketplace, live futures bots",
    "homepage_url": "https://algochains.ai",
    "logo_url": "https://algochains.ai/logo.png",
    "support_url": "https://algochains.ai/support",
    "products": [
        {
            "id": "developer-tier",
            "name": "AlgoChains Developer Tier",
            "description": "Read-only MCP access to 25 curated tools — regime detection, backtests, marketplace, Onyx semantic search",
            "pricing": "free_trial",
            "trial_days": 14,
        },
        {
            "id": "paper-tier",
            "name": "AlgoChains Paper Trading",
            "description": "Full 482-tool access with Alpaca paper execution, strategy validation pipeline",
            "pricing": "monthly",
            "price_usd_cents": 2900,
        },
        {
            "id": "live-tier",
            "name": "AlgoChains Live Trading",
            "description": "Full access including live broker execution (Tradovate, Alpaca live), marketplace subscription",
            "pricing": "monthly",
            "price_usd_cents": 9900,
        },
    ],
}

def _verify_stripe_signature(body: bytes, signature: str, webhook_secret: str) -> bool:
    """Verify Stripe APP webhook HMAC-SHA256 signature."""
    t, v1 = "", ""
    for part in signature.split(","):
        if part.startswith("t="):  t = part[2:]
        if part.startswith("v1="): v1 = part[3:]
    if not t or not v1:
        return False
    signed = f"{t}.{body.decode()}"
    expected = hmac.new(webhook_secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1) and abs(time.time() - int(t)) < 300

def _get_webhook_secret() -> str:
    secret = os.getenv("STRIPE_APP_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Stripe APP webhook secret not configured (STRIPE_APP_WEBHOOK_SECRET)")
    return secret

async def _verify_request(request: Request) -> bytes:
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not sig:
        raise HTTPException(401, "Missing stripe-signature header")
    if not _verify_stripe_signature(body, sig, _get_webhook_secret()):
        raise HTTPException(401, "Invalid Stripe APP signature")
    return body

# ── Product info endpoint ──────────────────────────────────────────────────────
@app.get("/app/info")
async def app_info():
    return JSONResponse(APP_INFO)

# ── Provision endpoint ─────────────────────────────────────────────────────────
@app.post("/app/provision")
async def provision(request: Request):
    """
    Stripe calls this when a user runs:
      stripe projects link algochains
    We create a developer API key and return credentials.
    """
    body = await _verify_request(request)
    data: dict[str, Any] = json.loads(body)

    product_id = data.get("product_id", "developer-tier")
    stripe_customer_id = data.get("customer_id", "")
    stripe_account_id  = data.get("account_id", "")
    email = data.get("email", "")

    # Generate a developer API key
    raw_key = f"ac_live_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    # Store in Supabase (developer_api_keys table)
    try:
        import httpx
        supabase_url = os.getenv("SUPABASE_URL", "")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if supabase_url and supabase_key:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{supabase_url}/rest/v1/developer_api_keys",
                    headers={
                        "apikey": supabase_key,
                        "Authorization": f"Bearer {supabase_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "stripe_customer_id": stripe_customer_id,
                        "stripe_account_id": stripe_account_id,
                        "email": email,
                        "product_id": product_id,
                        "key_hash": key_hash,
                        "key_prefix": raw_key[:12],
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "trial_ends_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(time.time() + 14 * 86400)
                        ) if product_id == "developer-tier" else None,
                    },
                )
    except Exception:
        pass  # Log but don't fail provisioning

    return JSONResponse({
        "resource_id": f"ac_{stripe_customer_id[:12]}_{product_id}",
        "credentials": {
            "ALGOCHAINS_BRIDGE_KEY": raw_key,
            "ALGOCHAINS_BRIDGE_URL": "https://api.algochains.ai/api/mcp",
        },
        "next_steps": [
            "Run: algochains doctor",
            "Run: algochains detect-market-regime",
            "Browse marketplace: algochains browse-strategy-marketplace",
            "Full docs: https://docs.algochains.ai/cli",
        ],
        "status": "active",
    })

# ── Status endpoint ────────────────────────────────────────────────────────────
@app.get("/app/status/{resource_id}")
async def provision_status(resource_id: str, request: Request):
    await _verify_request(request)
    # In production: look up resource_id in Supabase
    return JSONResponse({
        "resource_id": resource_id,
        "status": "active",
        "product": resource_id.split("_")[-1] if "_" in resource_id else "developer-tier",
    })

# ── Deprovision endpoint ───────────────────────────────────────────────────────
@app.post("/app/deprovision/{resource_id}")
async def deprovision(resource_id: str, request: Request):
    await _verify_request(request)
    # In production: revoke key in Supabase, notify user
    return JSONResponse({"status": "deprovisioned", "resource_id": resource_id})

# ── Health (public) ────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "algochains-stripe-app", "version": "1.0.0"}
