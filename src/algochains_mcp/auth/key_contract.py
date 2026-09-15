"""
key_contract.py — Single source of truth for AlgoChains developer API key generation.

Import this module from EVERY writer that creates developer_api_keys rows:
  - django_algochains/home/services/developer_key_service.py  (Writer A)
  - algochains_mcp/auth/platform_auth.py                      (Writer B)
  - stripe_app/server.py                                      (Writer C)

This ensures all three writers produce an identical INSERT column shape, which
is the prerequisite for writer-parity tests and correct bridge resolution.

Canonical product tiers are Free, Trader, Developer and Enterprise, with
Employee reserved for internal access. Only Developer, Enterprise and Employee
may mint developer-key scopes. Unknown tiers fail closed.

Key design:
  - Plaintext NEVER stored. Generated once, shown once, SHA-256 hashed for storage.
  - Prefix: "ac_live_" (production) or "ac_test_" (sandbox)
  - Hash: hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
  - Hint: first prefix + last 4 chars (display-safe)
  - Scopes: tier-differentiated; never auto-expanded without owner approval
"""
from __future__ import annotations

import hashlib
import secrets
import string
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

LIVE_PREFIX = "ac_live_"
TEST_PREFIX = "ac_test_"
DEVELOPER_KEY_PREFIXES = (LIVE_PREFIX, TEST_PREFIX)

_KEY_ALPHABET = string.ascii_letters + string.digits
_KEY_BODY_LEN = 40  # generates 40 random chars after the prefix

# Tier scope map — source of truth for all writers.
# IMPORTANT: expanding these requires owner approval AND a backfill migration.
_DEVELOPER_SCOPES = [
    "read:market_data",
    "read:signals",
    "read:backtest",
    "write:backtest",
    "agent:sandbox",
    "spend:llm_budget",
    # Owner-approved 2026-09-01: the Open List track is self-serve by design
    # (paper-only, UNVERIFIED, creator bound server-side; live/verified still
    # requires staff), so submit_to_marketplace is developer-tier, not enterprise.
    "publish:listing",
]

TIER_SCOPES: dict[str, list[str]] = {
    "developer": _DEVELOPER_SCOPES,
    # Employee is internal-only but exercises the same public Developer tool
    # contract. Special internal/tick rights are policy-gated separately rather
    # than encoded into a reusable API-key scope.
    "employee": _DEVELOPER_SCOPES,
    "enterprise": [
        *_DEVELOPER_SCOPES,
        "agent:host",
        "read:data_warehouse",
    ],
}

# Unknown tiers fail closed. Free/Trader customers must never gain market-data
# scopes just because a stale writer passed an unrecognized tier token.
DEFAULT_SCOPES: list[str] = []

# Legacy tier tokens. Aligned with Django_Algochains
# home/services/developer_entitlements.py (_canonical_tier, #930): legacy names
# such as developer_pro / pro are NOT accepted at the entitlement boundary and
# resolve to "free", which owns no developer-key scopes. Listed for docs/tests
# only; they intentionally do NOT alias to "developer".
LEGACY_FREE_TIER_TOKENS = frozenset({"developer_pro", "pro", "starter"})


class UnknownKeyTierError(ValueError):
    """Raised when a writer tries to mint a key for a tier with no key scopes."""


def canonical_key_tier(tier: str) -> str:
    """Return a canonical key-owning tier or an empty fail-closed token.

    Free, Trader, legacy (developer_pro/pro/starter) and unknown tiers all
    return "" and callers must refuse to mint.
    """
    normalized = "_".join(str(tier or "").strip().lower().replace("-", "_").split())
    return normalized if normalized in TIER_SCOPES else ""


# ── Key generation ─────────────────────────────────────────────────────────────
def generate_platform_key(env: str = "live") -> str:
    """
    Generate a raw developer platform key. SHOW ONCE — NEVER STORE PLAINTEXT.

    Returns: "ac_live_<40 random chars>"  or  "ac_test_<40 random chars>"
    """
    if env not in ("live", "test"):
        raise ValueError(f"env must be 'live' or 'test', got {env!r}")
    prefix = LIVE_PREFIX if env == "live" else TEST_PREFIX
    body = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_BODY_LEN))
    return f"{prefix}{body}"


def hash_platform_key(raw_key: str) -> str:
    """
    Return the SHA-256 hex digest. This is the ONLY value stored in the DB.
    Matches the hash computed in developer_key_service.py and developer_auth.py.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_hint(raw_key: str) -> str:
    """
    Return a display-safe hint (prefix + '...' + last 4 chars).
    Example: "ac_live_...5Lgh"
    """
    prefix = LIVE_PREFIX if raw_key.startswith(LIVE_PREFIX) else TEST_PREFIX
    return f"{prefix}...{raw_key[-4:]}"


def key_prefix_field(raw_key: str) -> str:
    """
    Return the first 12 chars of the key for the key_prefix column (legacy compat).
    Example: "ac_live_AbCd"
    """
    return raw_key[:12]


def prefix_field(raw_key: str) -> str:
    """
    Return just the bare prefix ("ac_live_" or "ac_test_") for the prefix column.
    """
    return LIVE_PREFIX if raw_key.startswith(LIVE_PREFIX) else TEST_PREFIX


# ── Scope utilities ────────────────────────────────────────────────────────────
def scopes_for_tier(
    tier: str,
    override: Optional[list[str]] = None,
) -> list[str]:
    """Return the canonical scope list for a supported key-owning tier.

    If override is provided, each scope is validated against the tier maximum.
    Unknown tiers return no scopes instead of inheriting market-data access.
    """
    canonical = canonical_key_tier(tier)
    allowed = set(TIER_SCOPES.get(canonical, DEFAULT_SCOPES))
    if override is not None:
        return [scope for scope in override if scope in allowed]
    return TIER_SCOPES.get(canonical, DEFAULT_SCOPES).copy()


# ── Validation helpers ─────────────────────────────────────────────────────────
def is_developer_key(key: str | None) -> bool:
    """True iff the key starts with a known developer prefix."""
    return bool(key) and any(key.startswith(p) for p in DEVELOPER_KEY_PREFIXES)


# ── Canonical INSERT shape ─────────────────────────────────────────────────────
def build_insert_payload(
    raw_key: str,
    clerk_user_id: str,
    tier: str,
    label: str = "Default",
    override_scopes: Optional[list[str]] = None,
    billing_account_id: Optional[str] = None,
    created_by_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """
    Build the canonical INSERT payload dict for public.developer_api_keys.

    ALL writers must produce this exact column set. Using this function ensures
    writer-parity and prevents schema drift.

    Fails closed: raises UnknownKeyTierError (so nothing is persisted) when the
    tier is unknown/empty/legacy or the resulting scope set is empty. A row with
    ``scopes=[]`` or ``tier_at_creation=""`` must never be written.
    """
    env = "test" if raw_key.startswith(TEST_PREFIX) else "live"
    canonical_tier = canonical_key_tier(tier)
    if not canonical_tier:
        raise UnknownKeyTierError(
            f"refusing to mint developer key: tier {tier!r} has no developer-key entitlement"
        )
    scopes = scopes_for_tier(canonical_tier, override_scopes)
    if not scopes:
        raise UnknownKeyTierError(
            "refusing to mint developer key: requested scopes are empty or outside the tier maximum"
        )
    return {
        "clerk_user_id": clerk_user_id,
        "key_hash": hash_platform_key(raw_key),
        "prefix": prefix_field(raw_key),
        "key_prefix": key_prefix_field(raw_key),
        "key_hint": key_hint(raw_key),
        "label": label[:60] if label else "Default",
        "name": label[:60] if label else "Default",
        "scopes": scopes,
        "tier_at_creation": canonical_tier,
        "env": env,
        "is_active": True,
        **({"billing_account_id": billing_account_id} if billing_account_id else {}),
        **({"created_by_ip": created_by_ip} if created_by_ip else {}),
        **({"user_agent": user_agent} if user_agent else {}),
    }


def build_core_mirror_payload(
    *,
    raw_key: str,
    developer_api_key_id: str,
    user_name: str,
    include_plaintext: bool = False,
) -> dict:
    """Build the transitional ``algochains-core`` mirror row.

    Hash-only is the safe default. ``include_plaintext`` exists solely for a
    time-bounded compatibility rollout and must be explicitly enabled by the
    operator while an old algochains-library-mcp consumer is upgraded.
    """
    payload = {
        "user_name": user_name,
        "developer_api_key_id": developer_api_key_id,
        "key_hash": hash_platform_key(raw_key),
        "key_prefix": key_prefix_field(raw_key),
        "is_active": True,
        "revoked_at": None,
    }
    if include_plaintext:
        payload["api_key"] = raw_key
    return payload
