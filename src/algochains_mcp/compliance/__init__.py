"""V9: Compliance Module — pre-trade checks, post-trade surveillance, audit trail."""
from .engine import ComplianceEngine
from .disclosures import (
    PAST_PERFORMANCE_DISCLAIMER,
    RISK_ACK_PHRASE,
    RISK_DISCLOSURE_VERSION,
    SUBSCRIBER_RISK_DISCLOSURE,
    TOS_VERSION,
    with_disclaimer,
)

__all__ = [
    "ComplianceEngine",
    "PAST_PERFORMANCE_DISCLAIMER",
    "RISK_ACK_PHRASE",
    "RISK_DISCLOSURE_VERSION",
    "SUBSCRIBER_RISK_DISCLOSURE",
    "TOS_VERSION",
    "with_disclaimer",
]
