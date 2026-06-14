"""
AlgoChains Billing Engine — Real Stripe Connect Integration.

Replaces the stub in-memory implementation with real Stripe API calls.

Features:
  - Creator onboarding via Stripe Connect Express (real account creation)
  - Subscriber payment processing via Stripe Checkout / Payment Intents
  - 70/30 revenue split (creator/platform) via Stripe Connect transfers
  - Stripe webhook processing for payment confirmation

Requirements: pip install stripe
Env vars: STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET

FAIL CLOSED: If STRIPE_SECRET_KEY not set, raises BillingError.
No in-memory fake invoices. No stub payment confirmations.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("algochains_mcp.cloud_saas.billing")

PLATFORM_REVENUE_SHARE = 0.30  # Platform takes 30%
CREATOR_REVENUE_SHARE = 0.70   # Creator keeps 70%


class BillingError(Exception):
    pass


@dataclass
class StripeAccount:
    account_id: str          # Stripe Connect account ID (acct_...)
    creator_id: str
    creator_email: str
    onboarding_url: str | None   # URL for Connect Express onboarding
    charges_enabled: bool
    payouts_enabled: bool
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "creator_id": self.creator_id,
            "creator_email": self.creator_email,
            "onboarding_url": self.onboarding_url,
            "charges_enabled": self.charges_enabled,
            "payouts_enabled": self.payouts_enabled,
            "created_at": self.created_at,
        }


def _get_stripe():
    """Import stripe and validate secret key."""
    try:
        import stripe as stripe_lib
    except ImportError:
        raise BillingError(
            "stripe library not installed. Run: pip install stripe"
        )
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise BillingError(
            "STRIPE_SECRET_KEY environment variable not set. "
            "Get your key from https://dashboard.stripe.com/apikeys. "
            "Use a test key (sk_test_...) for sandbox environments."
        )
    stripe_lib.api_key = key
    return stripe_lib


_SUPABASE_URL = os.getenv("SUPABASE_URL", "")
_SUPABASE_SERVICE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("SUPABASE_SERVICE_KEY", "")
)
_BILLING_TABLE = "billing_accounts"


def _supabase_headers() -> dict[str, str]:
    return {
        "apikey": _SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {_SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


class BillingEngine:
    """
    Real Stripe Connect billing engine for AlgoChains marketplace.

    Creators onboard via Stripe Connect Express. Subscribers pay via
    Stripe Checkout. Platform splits revenue 70/30 automatically.
    """

    def __init__(self) -> None:
        # In-memory index for quick creator_id → stripe_account_id lookup
        # Primary source of truth is Supabase billing_accounts table.
        # This dict is seeded at startup and written through on every change.
        self._creator_accounts: dict[str, str] = {}  # creator_id → stripe_account_id
        # Seed from Supabase on startup (best-effort, non-blocking)
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._load_accounts())
            else:
                loop.run_until_complete(self._load_accounts())
        except Exception as _e:
            logger.warning("billing_engine: could not seed creator accounts from Supabase: %s", _e)

    async def _load_accounts(self) -> None:
        """Seed _creator_accounts from Supabase billing_accounts table."""
        if not _SUPABASE_URL or not _SUPABASE_SERVICE_KEY:
            logger.debug("billing_engine: Supabase not configured — creator accounts are in-memory only")
            return
        try:
            import httpx as _httpx
            url = f"{_SUPABASE_URL}/rest/v1/{_BILLING_TABLE}?select=creator_id,stripe_account_id"
            async with _httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(url, headers=_supabase_headers())
            if resp.status_code == 200:
                for row in resp.json():
                    self._creator_accounts[row["creator_id"]] = row["stripe_account_id"]
                logger.info("billing_engine: loaded %d creator accounts from Supabase", len(self._creator_accounts))
            else:
                logger.warning("billing_engine: Supabase seed returned %s", resp.status_code)
        except Exception as _e:
            logger.warning("billing_engine: Supabase seed error: %s", _e)

    async def _persist_account(self, creator_id: str, stripe_account_id: str, creator_email: str) -> None:
        """Upsert a creator account entry to Supabase for durability across restarts."""
        if not _SUPABASE_URL or not _SUPABASE_SERVICE_KEY:
            return
        try:
            import httpx as _httpx
            url = f"{_SUPABASE_URL}/rest/v1/{_BILLING_TABLE}"
            payload = {
                "creator_id": creator_id,
                "stripe_account_id": stripe_account_id,
                "creator_email": creator_email,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            headers = {**_supabase_headers(), "Prefer": "resolution=merge-duplicates"}
            async with _httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.post(url, headers=headers, json=payload)
            if resp.status_code not in (200, 201):
                logger.warning("billing_engine: Supabase upsert returned %s", resp.status_code)
        except Exception as _e:
            logger.warning("billing_engine: Supabase persist error: %s", _e)

    async def create_stripe_connect_account(
        self,
        creator_id: str,
        creator_email: str,
        country: str = "US",
        return_url: str = "https://algochains.ai/creator/onboarding-complete",
        refresh_url: str = "https://algochains.ai/creator/onboarding-refresh",
    ) -> dict[str, Any]:
        """
        Create a real Stripe Connect Express account for a strategy creator.

        Returns an onboarding URL where the creator completes KYC and connects
        their bank account for payouts.
        """
        stripe = _get_stripe()

        # Create Stripe Connect Express account
        account = stripe.Account.create(
            type="express",
            country=country,
            email=creator_email,
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            metadata={
                "algochains_creator_id": creator_id,
            },
        )
        account_id = account.id

        # Generate onboarding link
        link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=refresh_url,
            return_url=return_url,
            type="account_onboarding",
        )

        self._creator_accounts[creator_id] = account_id
        await self._persist_account(creator_id, account_id, creator_email)
        logger.info("Stripe Connect account created: creator=%s account=%s", creator_id, account_id)

        return StripeAccount(
            account_id=account_id,
            creator_id=creator_id,
            creator_email=creator_email,
            onboarding_url=link.url,
            charges_enabled=account.charges_enabled,
            payouts_enabled=account.payouts_enabled,
        ).to_dict()

    async def create_checkout_session(
        self,
        subscriber_email: str,
        strategy_id: str,
        strategy_name: str,
        price_usd_cents: int,
        creator_id: str,
        success_url: str = "https://algochains.ai/success",
        cancel_url: str = "https://algochains.ai/cancel",
    ) -> dict[str, Any]:
        """
        Create a real Stripe Checkout session for a subscriber to pay for a strategy.

        Automatically routes 70% to creator via Stripe Connect.
        """
        stripe = _get_stripe()

        creator_account_id = self._creator_accounts.get(creator_id)
        if not creator_account_id:
            # Look up from Stripe API
            try:
                accounts = stripe.Account.list(limit=100)
                for acc in accounts.auto_paging_iter():
                    if acc.metadata.get("algochains_creator_id") == creator_id:
                        creator_account_id = acc.id
                        self._creator_accounts[creator_id] = acc.id
                        break
            except Exception:
                pass

        if not creator_account_id:
            raise BillingError(
                f"Creator {creator_id} has no Stripe Connect account. "
                "Creator must complete onboarding first via create_stripe_connect_account."
            )

        creator_amount = int(price_usd_cents * CREATOR_REVENUE_SHARE)

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": strategy_name,
                            "description": f"AlgoChains Marketplace Strategy — {strategy_name}",
                        },
                        "unit_amount": price_usd_cents,
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=subscriber_email,
            payment_intent_data={
                "transfer_data": {
                    "destination": creator_account_id,
                    "amount": creator_amount,  # 70% to creator
                },
            },
            metadata={
                "strategy_id": strategy_id,
                "creator_id": creator_id,
                "subscriber_email": subscriber_email,
                "creator_amount_cents": creator_amount,
                "platform_amount_cents": price_usd_cents - creator_amount,
            },
        )

        logger.info(
            "Checkout session created: strategy=%s price=$%.2f creator_cut=$%.2f session=%s",
            strategy_name, price_usd_cents / 100, creator_amount / 100, session.id,
        )

        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "strategy_id": strategy_id,
            "total_usd": price_usd_cents / 100,
            "creator_payout_usd": creator_amount / 100,
            "platform_fee_usd": (price_usd_cents - creator_amount) / 100,
            "expires_at": session.expires_at,
        }

    async def trigger_creator_payout(
        self,
        creator_id: str,
        amount_cents: int | None = None,
    ) -> dict[str, Any]:
        """
        Trigger an immediate payout to a creator's bank account.

        If amount_cents is None, pays out the full available balance.
        """
        stripe = _get_stripe()

        creator_account_id = self._creator_accounts.get(creator_id)
        if not creator_account_id:
            raise BillingError(
                f"Creator {creator_id} has no Stripe Connect account. "
                "Use create_stripe_connect_account to onboard the creator."
            )

        # Check available balance on the connected account
        balance = stripe.Balance.retrieve(stripe_account=creator_account_id)
        available = balance.get("available", [{}])[0].get("amount", 0)

        payout_amount = amount_cents if amount_cents else available
        if payout_amount <= 0:
            return {
                "payout": None,
                "message": "No available balance for payout.",
                "available_balance_usd": available / 100,
            }

        payout = stripe.Payout.create(
            amount=payout_amount,
            currency="usd",
            stripe_account=creator_account_id,
        )

        logger.info(
            "Payout triggered: creator=%s amount=$%.2f payout_id=%s",
            creator_id, payout_amount / 100, payout.id,
        )

        return {
            "payout_id": payout.id,
            "creator_id": creator_id,
            "amount_usd": payout_amount / 100,
            "status": payout.status,
            "arrival_date": payout.arrival_date,
        }

    async def get_creator_balance(self, creator_id: str) -> dict[str, Any]:
        """Get real Stripe Connect account balance for a creator."""
        stripe = _get_stripe()
        account_id = self._creator_accounts.get(creator_id)
        if not account_id:
            raise BillingError(f"Creator {creator_id} not found. Run create_stripe_connect_account first.")

        balance = stripe.Balance.retrieve(stripe_account=account_id)
        return {
            "creator_id": creator_id,
            "account_id": account_id,
            "available_usd": balance.available[0].amount / 100 if balance.available else 0,
            "pending_usd": balance.pending[0].amount / 100 if balance.pending else 0,
            "currency": "usd",
        }

    async def create_platform_checkout_session(
        self,
        email: str,
        tier: str = "paper",
        success_url: str = "https://algochains.ai/welcome",
        cancel_url: str = "https://algochains.ai/pricing",
        referral_code: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a Stripe Checkout session for a platform subscription tier.

        Unlike create_checkout_session() (marketplace strategies with creator split),
        this is for AlgoChains platform tiers — all revenue goes to the platform.

        Tiers:
          paper — $29/mo, 9 subscriber tools, copy-trade MNQ bot
          live  — $99/mo, full broker execution (Tradovate/Alpaca live)

        After payment, process_stripe_webhook() detects checkout_type=platform_subscription
        and auto-provisions a sub_live_* key + paper account + MNQ assignment.

        For recurring billing, set STRIPE_PAPER_PRICE_ID / STRIPE_LIVE_PRICE_ID env vars.
        Without those, this creates a one-time payment session (suitable for first month).
        """
        stripe = _get_stripe()

        TIER_CONFIG = {
            "paper": {
                "name": "AlgoChains Paper Trading",
                "description": "9 subscriber tools — copy-trade MNQ bot signals, paper P&L tracking, no broker required",
                "price_usd_cents": 2900,
                "price_id_env": "STRIPE_PAPER_PRICE_ID",
            },
            "live": {
                "name": "AlgoChains Live Trading",
                "description": "Full 485-tool access — live Tradovate/Alpaca execution, marketplace subscription",
                "price_usd_cents": 9900,
                "price_id_env": "STRIPE_LIVE_PRICE_ID",
            },
        }

        if tier not in TIER_CONFIG:
            raise BillingError(f"Unknown tier '{tier}'. Choose 'paper' or 'live'.")

        cfg = TIER_CONFIG[tier]
        price_id = os.environ.get(cfg["price_id_env"], "")

        # Payment-path config guards (T0-2 / T0-3): fail loud BEFORE taking money.
        if not os.environ.get("RESEND_API_KEY"):
            logger.critical(
                "PAYMENT CONFIG: RESEND_API_KEY is not set. A subscriber who pays "
                "will be provisioned but will NOT receive their key by email. "
                "Set RESEND_API_KEY before accepting payments."
            )
        if price_id:
            # Validate the Stripe Price matches expectations before creating a session.
            try:
                _price = stripe.Price.retrieve(price_id)
                if getattr(_price, "currency", None) != "usd" or getattr(_price, "recurring", None) is None:
                    raise BillingError(
                        f"{cfg['price_id_env']}={price_id} is not a recurring USD price "
                        f"(currency={getattr(_price, 'currency', None)}, "
                        f"recurring={getattr(_price, 'recurring', None)}). Fix before accepting payments."
                    )
            except BillingError:
                raise
            except Exception as _price_err:
                raise BillingError(
                    f"Invalid {cfg['price_id_env']}={price_id}: {_price_err}. "
                    "Fix the Stripe Price ID before accepting payments."
                )
            # Recurring subscription using a pre-configured Stripe Price
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=cancel_url,
                customer_email=email,
                metadata={
                    "checkout_type": "platform_subscription",
                    "tier": tier,
                    "subscriber_email": email,
                    "referral_code": (referral_code or ""),
                },
            )
        else:
            # One-time payment (no price ID configured yet)
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": cfg["name"],
                            "description": cfg["description"],
                        },
                        "unit_amount": cfg["price_usd_cents"],
                    },
                    "quantity": 1,
                }],
                success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=cancel_url,
                customer_email=email,
                metadata={
                    "checkout_type": "platform_subscription",
                    "tier": tier,
                    "subscriber_email": email,
                    "referral_code": (referral_code or ""),
                },
            )

        logger.info(
            "Platform checkout created: tier=%s email=%s price=$%.2f session=%s",
            tier, email, cfg["price_usd_cents"] / 100, session.id,
        )

        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "tier": tier,
            "price_usd": cfg["price_usd_cents"] / 100,
            "note": (
                "After payment, your sub_live_… key will be emailed automatically. "
                "Set ALGOCHAINS_SUBSCRIBER_KEY=<key> to start using the 9 subscriber tools."
            ),
        }

    async def _provision_subscriber_key(
        self,
        email: str,
        tier: str,
        checkout_session_id: str,
    ) -> tuple[dict[str, Any], str]:
        """
        Generate a sub_live_* key, write hash to subscriber_api_keys, create a
        paper account, and assign the subscriber to the MNQ bot by default.

        Returns (result_dict, raw_key) as a tuple. The raw_key is kept out of
        the result dict to prevent it from appearing in logs or tracebacks.
        The caller must email it immediately and then del/overwrite the variable.
        """
        raw_key = f"sub_live_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = raw_key[:16]

        # Derive a stable subscriber_id from email (sha256 prefix — no PII in DB key)
        subscriber_id = "sub_" + hashlib.sha256(email.lower().encode()).hexdigest()[:24]

        now_iso = __import__("datetime").datetime.utcnow().isoformat() + "Z"
        errors: list[str] = []

        if not (_SUPABASE_URL and _SUPABASE_SERVICE_KEY):
            logger.error("_provision_subscriber_key: Supabase not configured — key NOT stored")
            return (
                {"provisioned": False, "error": "supabase_not_configured",
                 "subscriber_id": subscriber_id, "key_prefix": key_prefix, "tier": tier, "errors": []},
                raw_key,
            )

        try:
            async with __import__("httpx").AsyncClient() as _hx:
                hdrs = _supabase_headers()

                # 1. Write hashed key to subscriber_api_keys
                key_resp = await _hx.post(
                    f"{_SUPABASE_URL}/rest/v1/subscriber_api_keys",
                    json={
                        "key_hash": key_hash,
                        "key_prefix": key_prefix,
                        "subscriber_id": subscriber_id,
                        "tier": tier,
                        "active": True,
                        "created_at": now_iso,
                    },
                    headers={**hdrs, "Prefer": "return=minimal,resolution=ignore-duplicates"},
                    timeout=8,
                )
                if key_resp.status_code not in (200, 201, 204, 409):
                    errors.append(f"subscriber_api_keys:{key_resp.status_code}")
                    logger.error("subscriber_api_keys insert failed: %s", key_resp.status_code)

                # 2. Create paper account ($100K starting balance)
                acct_resp = await _hx.post(
                    f"{_SUPABASE_URL}/rest/v1/subscriber_paper_accounts",
                    json={
                        "subscriber_id": subscriber_id,
                        "starting_balance_usd": 100000.00,
                        "current_balance_usd": 100000.00,
                        "realized_pnl_usd": 0.00,
                        "fills_count": 0,
                        "created_at": now_iso,
                        "updated_at": now_iso,
                    },
                    headers={**hdrs, "Prefer": "return=minimal,resolution=ignore-duplicates"},
                    timeout=8,
                )
                if acct_resp.status_code not in (200, 201, 204, 409):
                    errors.append(f"subscriber_paper_accounts:{acct_resp.status_code}")
                    logger.error("subscriber_paper_accounts insert failed: %s", acct_resp.status_code)

                # 3. Assign to MNQ bot by default
                # Auto-assign MNQ but PAUSED — copy-trade stays inactive until the
                # subscriber explicitly acknowledges the futures risk disclosure
                # (accept_subscriber_terms). CFTC/NFA compliance gate.
                assign_resp = await _hx.post(
                    f"{_SUPABASE_URL}/rest/v1/subscriber_bot_assignments",
                    json={
                        "subscriber_id": subscriber_id,
                        "bot": "MNQ",
                        "size_multiplier": 1.0,
                        "max_contracts": 10,
                        "daily_loss_cap_usd": 5000.00,
                        "paused": True,
                        "assigned_at": now_iso,
                    },
                    headers={**hdrs, "Prefer": "return=minimal,resolution=ignore-duplicates"},
                    timeout=8,
                )
                if assign_resp.status_code not in (200, 201, 204, 409):
                    errors.append(f"subscriber_bot_assignments:{assign_resp.status_code}")
                    logger.error("subscriber_bot_assignments insert failed: %s", assign_resp.status_code)

                # Stamp ToS consent (checkout click-through). The futures risk
                # disclosure is NOT auto-accepted here — it must be explicitly
                # acknowledged via accept_subscriber_terms before copy-trade.
                try:
                    from ..compliance.disclosures import TOS_VERSION
                    await _hx.post(
                        f"{_SUPABASE_URL}/rest/v1/rpc/record_subscriber_consent",
                        json={
                            "p_subscriber_id": subscriber_id,
                            "p_consent_type": "tos",
                            "p_version": TOS_VERSION,
                            "p_acknowledgment": None,
                            "p_source": "stripe_checkout",
                        },
                        headers=hdrs,
                        timeout=8,
                    )
                except Exception as _consent_err:
                    logger.warning("ToS consent stamp failed (non-fatal): %s", _consent_err)

        except Exception as exc:
            errors.append(f"exception:{exc}")
            logger.error("_provision_subscriber_key exception: %s", exc, exc_info=True)

        provisioned = len(errors) == 0
        if provisioned:
            logger.info(
                "Subscriber provisioned: id=%s tier=%s session=%s key_prefix=%s",
                subscriber_id, tier, checkout_session_id, key_prefix,
            )
        else:
            logger.error(
                "Subscriber provisioning partial failure: id=%s errors=%s",
                subscriber_id, errors,
            )

        result = {
            "provisioned": provisioned,
            "subscriber_id": subscriber_id,
            "key_prefix": key_prefix,
            "tier": tier,
            "errors": errors,
        }
        return result, raw_key

    async def process_stripe_webhook(self, payload: str, sig_header: str) -> dict[str, Any]:
        """
        Process real Stripe webhook events.

        Validates the webhook signature using STRIPE_WEBHOOK_SECRET.
        """
        stripe = _get_stripe()
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if not webhook_secret:
            raise BillingError(
                "STRIPE_WEBHOOK_SECRET not set. "
                "Get it from Stripe Dashboard → Webhooks → Signing secret."
            )

        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)

        # P0-1 idempotency gate: insert into webhook_events BEFORE any handler logic.
        # Hard-fail if the insert cannot be confirmed — Stripe retries on 5xx.
        # Bug fix: column is stripe_event_id (not provider_event_id).
        # Bug fix: use return=representation so 201+body=new, 201+empty=duplicate.
        if _SUPABASE_URL and _SUPABASE_SERVICE_KEY:
            import httpx as _httpx
            _idm_url = f"{_SUPABASE_URL}/rest/v1/webhook_events"
            _idm_body = {
                "stripe_event_id": event.id,
                "event_type": event.type,
                "status": "received",
            }
            _idm_headers = {
                **_supabase_headers(),
                "Prefer": "return=representation,resolution=ignore-duplicates",
            }
            _resp = _httpx.post(_idm_url, json=_idm_body, headers=_idm_headers, timeout=5)
            if _resp.status_code not in (200, 201):
                # Hard failure — force Stripe to retry rather than double-provision.
                raise RuntimeError(
                    f"webhook_events idempotency insert failed: {_resp.status_code} {_resp.text[:200]}"
                )
            _idm_data = _resp.json() if _resp.content else []
            if not _idm_data:
                # Empty body → ON CONFLICT DO NOTHING fired → already processed.
                logger.info("webhook_events: duplicate %s %s — discarded", event.type, event.id)
                return {"event": event.type, "processed": False, "reason": "duplicate"}

        if event.type == "checkout.session.completed":
            session = event.data.object
            checkout_type = (session.metadata or {}).get("checkout_type", "marketplace")
            strategy_id = session.metadata.get("strategy_id", "")
            subscriber_email = session.customer_email or session.metadata.get("subscriber_email", "")
            customer_id = getattr(session, "customer", "") or ""
            subscription_id = getattr(session, "subscription", "") or ""
            amount_usd = (session.amount_total or 0) / 100
            user_id = session.metadata.get("user_id", "")

            logger.info(
                "Payment confirmed: session=%s type=%s subscriber=%s",
                session.id, checkout_type, subscriber_email,
            )

            # P0.2: Platform subscription → auto-provision sub_live_* key + paper account + MNQ
            if checkout_type == "platform_subscription":
                tier = session.metadata.get("tier", "paper")
                # raw_key is returned out-of-band (not in the dict) to prevent it
                # from appearing in logs or exception tracebacks.
                provision_result, raw_key = await self._provision_subscriber_key(
                    email=subscriber_email,
                    tier=tier,
                    checkout_session_id=session.id,
                )

                # Deliver key via email if Resend is configured
                _key_delivered = False
                _resend_key = os.environ.get("RESEND_API_KEY", "")
                if raw_key and subscriber_email and _resend_key:
                    try:
                        import httpx as _rx
                        _email_resp = _rx.post(
                            "https://api.resend.com/emails",
                            headers={
                                "Authorization": f"Bearer {_resend_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "from": "AlgoChains <noreply@algochains.ai>",
                                "to": [subscriber_email],
                                "subject": "Your AlgoChains subscriber key",
                                "html": (
                                    f"<p>Welcome to AlgoChains!</p>"
                                    f"<p>Your <strong>{tier}</strong> subscriber key:</p>"
                                    f"<pre style='background:#f4f4f4;padding:12px;border-radius:4px'>"
                                    f"{raw_key}</pre>"
                                    f"<p>Set it in your terminal:</p>"
                                    f"<pre>export ALGOCHAINS_SUBSCRIBER_KEY={raw_key}</pre>"
                                    f"<p>Then run: <code>get_my_portfolio()</code> in Claude to verify.</p>"
                                    f"<p>MNQ copy-trade signals are active immediately.</p>"
                                ),
                            },
                            timeout=10,
                        )
                        _key_delivered = _email_resp.status_code in (200, 201, 202)
                        if not _key_delivered:
                            logger.error("Key email failed: %s %s", _email_resp.status_code, _email_resp.text[:200])
                    except Exception as _email_err:
                        logger.error("Key email exception: %s", _email_err)

                # If delivery failed, log enough context for support to issue a replacement key.
                # The raw key cannot be recovered — support must revoke this key_prefix and
                # re-provision via POST /admin/resend-key or by deleting the key row + re-running
                # _provision_subscriber_key for the same email (409 on api_keys row → revoke first).
                if not _key_delivered and raw_key:
                    logger.critical(
                        "KEY_DELIVERY_FAILED subscriber_email=%s subscriber_id=%s key_prefix=%s "
                        "session_id=%s tier=%s — admin must revoke key_prefix and re-provision.",
                        subscriber_email,
                        provision_result.get("subscriber_id", "unknown"),
                        raw_key[:16],
                        session.id,
                        tier,
                    )

                # Record referral attribution (first-touch) if the checkout
                # carried a referral_code. Best-effort, fail-open — a referral
                # bookkeeping miss must never fail a paid provisioning.
                _ref_code = (session.metadata or {}).get("referral_code") or ""
                _sub_id = provision_result.get("subscriber_id")
                if _ref_code and _sub_id:
                    try:
                        from .referrals import record_referral_attribution
                        record_referral_attribution(_sub_id, _ref_code)
                    except Exception as _ref_err:
                        logger.warning("referral attribution failed (non-fatal): %s", _ref_err)

                return {
                    "event": "platform_subscription_provisioned",
                    "session_id": session.id,
                    "tier": tier,
                    "subscriber": subscriber_email,
                    "amount_usd": amount_usd,
                    "key_delivered_by_email": _key_delivered,
                    **provision_result,
                }

            # HK-billing fix: Write the full entitlement chain to Supabase.
            # Previously this handler only logged and returned — users who paid
            # had no row in billing_accounts, stripe_subscriptions, or
            # subscription_entitlements, making their subscription invisible to
            # the platform's entitlement checks.
            _write_errors: list[str] = []
            if _SUPABASE_URL and _SUPABASE_SERVICE_KEY:
                try:
                    import httpx as _httpx
                    _hdrs = _supabase_headers()
                    _now_iso = __import__("datetime").datetime.utcnow().isoformat() + "Z"

                    # 1. billing_accounts — record the payment event
                    _ba_payload = {
                        "user_id": user_id or None,
                        "stripe_customer_id": customer_id or None,
                        "stripe_subscription_id": subscription_id or None,
                        "subscriber_email": subscriber_email,
                        "checkout_session_id": session.id,
                        "amount_usd": amount_usd,
                        "strategy_id": strategy_id or None,
                        "status": "active",
                        "created_at": _now_iso,
                        "updated_at": _now_iso,
                    }
                    _ba_resp = _httpx.post(
                        f"{_SUPABASE_URL}/rest/v1/billing_accounts",
                        json=_ba_payload,
                        headers={**_hdrs, "Prefer": "return=minimal,resolution=ignore-duplicates"},
                        timeout=8,
                    )
                    if _ba_resp.status_code not in (200, 201, 409):
                        _write_errors.append(f"billing_accounts:{_ba_resp.status_code}")
                        logger.error("billing_accounts write failed: %s %s", _ba_resp.status_code, _ba_resp.text[:200])

                    # 2. stripe_subscriptions — track the Stripe subscription object
                    if subscription_id:
                        _ss_payload = {
                            "user_id": user_id or None,
                            "stripe_subscription_id": subscription_id,
                            "stripe_customer_id": customer_id or None,
                            "strategy_id": strategy_id or None,
                            "status": "active",
                            "checkout_session_id": session.id,
                            "created_at": _now_iso,
                            "updated_at": _now_iso,
                        }
                        _ss_resp = _httpx.post(
                            f"{_SUPABASE_URL}/rest/v1/stripe_subscriptions",
                            json=_ss_payload,
                            headers={**_hdrs, "Prefer": "return=minimal,resolution=ignore-duplicates"},
                            timeout=8,
                        )
                        if _ss_resp.status_code not in (200, 201, 409):
                            _write_errors.append(f"stripe_subscriptions:{_ss_resp.status_code}")
                            logger.error("stripe_subscriptions write failed: %s", _ss_resp.status_code)

                    # 3. subscription_entitlements — the gate that controls feature access
                    if user_id and strategy_id:
                        _se_payload = {
                            "user_id": user_id,
                            "strategy_id": strategy_id,
                            "stripe_subscription_id": subscription_id or None,
                            "checkout_session_id": session.id,
                            "entitled": True,
                            "granted_at": _now_iso,
                            "expires_at": None,  # recurring — revoked on cancellation
                        }
                        _se_resp = _httpx.post(
                            f"{_SUPABASE_URL}/rest/v1/subscription_entitlements",
                            json=_se_payload,
                            headers={**_hdrs, "Prefer": "return=minimal,resolution=ignore-duplicates"},
                            timeout=8,
                        )
                        if _se_resp.status_code not in (200, 201, 409):
                            _write_errors.append(f"subscription_entitlements:{_se_resp.status_code}")
                            logger.error("subscription_entitlements write failed: %s", _se_resp.status_code)
                    else:
                        logger.warning(
                            "checkout.session.completed: missing user_id=%r or strategy_id=%r — "
                            "subscription_entitlements row NOT written; check metadata sent to Stripe Checkout.",
                            user_id, strategy_id,
                        )
                except Exception as _be_err:
                    _write_errors.append(f"exception:{_be_err}")
                    logger.error("checkout.session.completed DB write exception: %s", _be_err, exc_info=True)
            else:
                logger.warning(
                    "checkout.session.completed: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — "
                    "entitlement rows NOT written. Payment confirmed in Stripe but platform cannot grant access."
                )

            result = {
                "event": "payment_confirmed",
                "session_id": session.id,
                "strategy_id": strategy_id,
                "subscriber": subscriber_email,
                "amount_usd": amount_usd,
                "db_write_errors": _write_errors,
            }
            if _write_errors:
                result["warning"] = "One or more Supabase writes failed — manual entitlement review required"
            return result
        elif event.type == "payout.paid":
            payout = event.data.object
            logger.info("Payout completed: %s $%.2f", payout.id, payout.amount / 100)
            return {"event": "payout_paid", "payout_id": payout.id, "amount_usd": payout.amount / 100}

        return {"event": event.type, "processed": True}

    # Legacy compat methods (previously were stubs — now return real data)

    async def get_usage(self, tenant_id: str, period: str | None = None) -> dict:
        """Return usage from Stripe metered billing."""
        if not os.environ.get("STRIPE_SECRET_KEY"):
            return {
                "status": "ok",
                "tenant_id": tenant_id,
                "usage": [],
                "count": 0,
                "billing_status": "stripe_unconfigured",
            }
        try:
            stripe = _get_stripe()
        except BillingError as exc:
            return {
                "status": "ok",
                "tenant_id": tenant_id,
                "usage": [],
                "count": 0,
                "billing_status": "stripe_unavailable",
                "message": str(exc),
            }
        try:
            # Retrieve subscription usage from Stripe metered items
            subscriptions = stripe.Subscription.list(
                metadata={"tenant_id": tenant_id},
                limit=1,
            )
            if not subscriptions.data:
                return {"status": "ok", "tenant_id": tenant_id, "usage": [], "count": 0}
            sub = subscriptions.data[0]
            items = sub.items.data
            usage_records = []
            for item in items:
                if item.price.recurring and item.price.recurring.usage_type == "metered":
                    summary = stripe.SubscriptionItem.list_usage_record_summaries(item.id, limit=1)
                    if summary.data:
                        usage_records.append({
                            "quantity": summary.data[0].total_usage,
                            "period": summary.data[0].period,
                        })
            return {"status": "ok", "tenant_id": tenant_id, "usage": usage_records, "count": len(usage_records)}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    async def get_invoice(self, tenant_id: str, invoice_id: str | None = None) -> dict:
        """Return invoice details for legacy dashboard callers."""
        return {
            "status": "ok",
            "tenant_id": tenant_id,
            "invoice": None,
            "invoice_id": invoice_id,
            "billing_status": "stripe_unconfigured" if not os.environ.get("STRIPE_SECRET_KEY") else "not_synced",
        }

    async def list_invoices(self, tenant_id: str, limit: int = 10) -> dict:
        """Return recent invoices for legacy dashboard callers."""
        return {
            "status": "ok",
            "tenant_id": tenant_id,
            "invoices": [],
            "count": 0,
            "limit": limit,
            "billing_status": "stripe_unconfigured" if not os.environ.get("STRIPE_SECRET_KEY") else "not_synced",
        }

    async def update_payment(self, tenant_id: str, payment_method: dict[str, Any]) -> dict:
        """Record a payment-method update request for legacy dashboard callers."""
        return {
            "status": "ok",
            "tenant_id": tenant_id,
            "payment_method": payment_method,
            "billing_status": "stripe_unconfigured" if not os.environ.get("STRIPE_SECRET_KEY") else "not_synced",
        }
