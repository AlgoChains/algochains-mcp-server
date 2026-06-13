"""
Canonical legal disclosures and consent versioning — single source of truth.

CFTC/NFA posture: AlgoChains is a SOFTWARE TOOL PROVIDER, not a registered
Commodity Trading Advisor (CTA) or investment adviser. Futures copy-trade
signals are informational. Before a subscriber can actively copy-trade a live
futures bot, they must explicitly acknowledge the futures risk disclosure;
that acknowledgment is persisted and audit-trailed (see
subscriber_consent_log + subscriber_api_keys consent columns).

Bump the *_VERSION strings whenever the disclosure text changes — a version
bump invalidates prior acknowledgments and forces re-consent on the next
gated action.
"""
from __future__ import annotations

from typing import Any

# Reuse the canonical futures risk disclosure already shown in the broker
# onboarding flow so subscribers and broker-connectors see identical text.
try:
    from ..onboarding import RISK_DISCLOSURE as _ONBOARDING_RISK_DISCLOSURE
except Exception:  # pragma: no cover - defensive: onboarding import is optional
    _ONBOARDING_RISK_DISCLOSURE = ""

# ─── Version stamps (bump to force re-consent) ────────────────────────────────
RISK_DISCLOSURE_VERSION = "2026-06-13"
TOS_VERSION = "2026-06-13"

# The exact acknowledgment string a subscriber must echo to consent.
RISK_ACK_PHRASE = (
    "I have read and understand the risk disclosure above. "
    "I accept full responsibility for my trading decisions."
)

# Futures risk disclosure (full). Falls back to an inline copy if the
# onboarding module is unavailable at import time.
SUBSCRIBER_RISK_DISCLOSURE = _ONBOARDING_RISK_DISCLOSURE or (
    "FUTURES TRADING INVOLVES SUBSTANTIAL RISK OF LOSS AND IS NOT SUITABLE FOR "
    "ALL INVESTORS. PAST PERFORMANCE IS NOT INDICATIVE OF FUTURE RESULTS. "
    "AlgoChains is a software tool provider, not a registered CTA or investment "
    "adviser, and does not manage money or give trading advice. You may lose "
    "more than your initial investment. Consult a licensed financial advisor "
    "before trading with real money."
)

# Short disclaimer attached to every performance-bearing payload (P&L,
# marketplace listings, portfolio snapshots, status). Required because any
# display of trading results must carry a past-performance / not-advice notice.
PAST_PERFORMANCE_DISCLAIMER = (
    "Past performance is not indicative of future results. Figures may include "
    "hypothetical or simulated (paper) results, which have inherent limitations "
    "and do not represent actual trading. AlgoChains is a software tool provider, "
    "not a registered CTA or investment adviser, and this is not investment advice."
)


def with_disclaimer(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach the standard past-performance disclaimer to a result payload.

    Idempotent and non-destructive — never overwrites an existing key.
    """
    if isinstance(payload, dict) and "disclaimer" not in payload:
        payload["disclaimer"] = PAST_PERFORMANCE_DISCLAIMER
    return payload


__all__ = [
    "RISK_DISCLOSURE_VERSION",
    "TOS_VERSION",
    "RISK_ACK_PHRASE",
    "SUBSCRIBER_RISK_DISCLOSURE",
    "PAST_PERFORMANCE_DISCLAIMER",
    "with_disclaimer",
]
