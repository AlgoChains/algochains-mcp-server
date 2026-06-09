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

import json
import logging
import os
import time
import uuid
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
        # ON CONFLICT DO NOTHING returns 0 rows if already processed → discard.
        if _SUPABASE_URL and _SUPABASE_SERVICE_KEY:
            try:
                import httpx as _httpx
                _idm_url = f"{_SUPABASE_URL}/rest/v1/webhook_events"
                _idm_body = {
                    "provider": "stripe",
                    "provider_event_id": event.id,
                    "event_type": event.type,
                }
                _idm_headers = {**_supabase_headers(), "Prefer": "return=minimal,resolution=ignore-duplicates"}
                _resp = _httpx.post(_idm_url, json=_idm_body, headers=_idm_headers, timeout=5)
                if _resp.status_code == 409 or (
                    _resp.status_code in (200, 201) and _resp.content == b""
                ):
                    # Already processed (conflict) — discard to prevent double-activation
                    logger.info("webhook_events: duplicate %s %s — discarded", event.type, event.id)
                    return {"event": event.type, "processed": False, "reason": "duplicate"}
                _resp.raise_for_status()
            except Exception as _idm_err:
                logger.warning("webhook_events idempotency insert failed (non-fatal): %s", _idm_err)

        if event.type == "checkout.session.completed":
            session = event.data.object
            strategy_id = session.metadata.get("strategy_id", "")
            subscriber_email = session.customer_email or ""
            customer_id = getattr(session, "customer", "") or ""
            subscription_id = getattr(session, "subscription", "") or ""
            amount_usd = (session.amount_total or 0) / 100
            user_id = session.metadata.get("user_id", "")

            logger.info(
                "Payment confirmed: session=%s strategy=%s subscriber=%s",
                session.id, strategy_id, subscriber_email,
            )

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
        stripe = _get_stripe()
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
