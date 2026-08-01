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
import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="AlgoChains Stripe APP", version="1.0.0")

# ── Stripe APP spec: product info ─────────────────────────────────────────────
APP_INFO = {
    "name": "AlgoChains",
    "description": "AI-native algorithmic trading CLI — 533 MCP tools, strategy marketplace, live futures bots",
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
            "description": "Full 533-tool access with Alpaca paper execution, strategy validation pipeline",
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
    from algochains_mcp.auth.key_contract import (
        build_core_mirror_payload,
        build_insert_payload,
        generate_platform_key,
    )

    body = await _verify_request(request)
    data: dict[str, Any] = json.loads(body)

    product_id = data.get("product_id", "developer-tier")
    stripe_customer_id = data.get("customer_id", "")
    stripe_account_id  = data.get("account_id", "")
    email = data.get("email", "")

    # Map Stripe product → AlgoChains tier
    tier = "enterprise" if product_id == "enterprise-tier" else "developer_pro"

    # Use email as clerk_user_id until Stripe webhook can supply a Clerk ID.
    # This is acceptable — bridge resolution needs clerk_user_id NOT NULL.
    # When Clerk is live, Stripe webhooks should include metadata.clerk_user_id.
    clerk_user_id = (
        data.get("metadata", {}).get("clerk_user_id")
        or email
        or f"stripe:{stripe_customer_id}"
    )

    raw_key = generate_platform_key(env="live")
    payload = build_insert_payload(
        raw_key=raw_key,
        clerk_user_id=clerk_user_id,
        tier=tier,
        label=f"Stripe {product_id}",
    )
    # Record Stripe-specific identifiers in the notes/metadata fields if available
    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    key_stored = False
    if supabase_url and supabase_key:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{supabase_url}/rest/v1/developer_api_keys",
                    headers={
                        "apikey": supabase_key,
                        "Authorization": f"Bearer {supabase_key}",
                        "Content-Type": "application/json",
                        # return=representation (not minimal) so we get the row id
                        # back to mirror into algochains-core below.
                        "Prefer": "return=representation",
                    },
                    json=payload,
                )
                if resp.status_code in (200, 201, 204):
                    key_stored = True
                    # Unify: mirror this key into algochains-core so it also
                    # grants access to algochains-library-mcp. Best-effort —
                    # same httpx client/connection, still inside the `async
                    # with` block so the client isn't already closed.
                    try:
                        row = resp.json()
                        row_id = row[0].get("id") if isinstance(row, list) and row else None
                        if row_id:
                            await client.post(
                                f"{supabase_url}/rest/v1/algochains-core",
                                headers={
                                    "apikey": supabase_key,
                                    "Authorization": f"Bearer {supabase_key}",
                                    "Content-Type": "application/json",
                                    "Prefer": "return=minimal",
                                },
                                json=build_core_mirror_payload(
                                    raw_key=raw_key,
                                    developer_api_key_id=str(row_id),
                                    user_name=email or clerk_user_id,
                                    include_plaintext=os.getenv(
                                        "ALGOCHAINS_CORE_PLAINTEXT_KEY_FALLBACK", ""
                                    ).lower() in {"1", "true", "yes"},
                                ),
                            )
                    except Exception:
                        # Deliberately do not log the exception object/message: the
                        # request body for this call contains the raw plaintext key,
                        # and some HTTP client error strings echo request internals.
                        log.warning("Stripe APP: algochains-core mirror failed")
                else:
                    log.error(
                        "Stripe APP: key storage failed HTTP %s: %s",
                        resp.status_code, resp.text[:200],
                    )
        except Exception as exc:
            log.error("Stripe APP: key storage exception: %s", exc)

    if not key_stored:
        log.warning(
            "Stripe APP: provisioning key for %s but storage failed — key may be unusable",
            clerk_user_id,
        )

    return JSONResponse({
        "resource_id": f"ac_{stripe_customer_id[:12]}_{product_id}",
        "credentials": {
            "ALGOCHAINS_API_KEY": raw_key,
            "ALGOCHAINS_BRIDGE_URL": "https://mcp.algochains.ai/api/mcp",
        },
        "next_steps": [
            "export ALGOCHAINS_API_KEY=<your key>",
            "Run: algochains doctor",
            "Run: algochains detect-market-regime",
            "Browse marketplace: algochains browse-strategy-marketplace",
            "Full docs: https://algochains.ai/docs/developer/",
        ],
        "status": "active" if key_stored else "provisioned_storage_pending",
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
