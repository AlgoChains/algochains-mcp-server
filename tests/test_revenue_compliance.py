"""
Unit tests for the revenue + compliance workstreams (WS1–WS6) and the
CFTC/NFA disclosure controls. These are hermetic — no Supabase, no Stripe,
no network. Modules that need a DB client are exercised via their fail-closed
paths or pure functions.
"""
from __future__ import annotations

import importlib

import pytest


# ─── Compliance disclosures ───────────────────────────────────────────────────

def test_past_performance_disclaimer_attached_idempotently():
    from algochains_mcp.compliance.disclosures import (
        PAST_PERFORMANCE_DISCLAIMER,
        with_disclaimer,
    )
    out = with_disclaimer({"pnl": 1})
    assert out["disclaimer"] == PAST_PERFORMANCE_DISCLAIMER
    # idempotent — never overwrites an existing disclaimer
    assert with_disclaimer({"disclaimer": "x"})["disclaimer"] == "x"


def test_hypothetical_disclaimer_is_cftc_441b_and_idempotent():
    from algochains_mcp.compliance.disclosures import (
        HYPOTHETICAL_PERFORMANCE_DISCLAIMER,
        with_hypothetical_disclaimer,
    )
    # Hallmark phrases of the CFTC Reg. 4.41(b) prescribed statement.
    text = HYPOTHETICAL_PERFORMANCE_DISCLAIMER
    assert "HYPOTHETICAL OR SIMULATED PERFORMANCE RESULTS" in text
    assert "BENEFIT OF HINDSIGHT" in text
    assert "DO NOT REPRESENT ACTUAL TRADING" in text
    out = with_hypothetical_disclaimer({"pnl": 1})
    assert out["hypothetical_performance_disclaimer"] == text
    assert "disclaimer" in out  # also carries the general one
    # idempotent
    pre = {"hypothetical_performance_disclaimer": "keep", "disclaimer": "keep2"}
    out2 = with_hypothetical_disclaimer(pre)
    assert out2["hypothetical_performance_disclaimer"] == "keep"


def test_risk_disclosure_versions_present():
    from algochains_mcp.compliance.disclosures import (
        RISK_ACK_PHRASE,
        RISK_DISCLOSURE_VERSION,
        TOS_VERSION,
    )
    assert RISK_DISCLOSURE_VERSION and TOS_VERSION
    assert "full responsibility" in RISK_ACK_PHRASE


# ─── WS4: high-water-mark performance fee ─────────────────────────────────────

def test_hwm_drawdown_recovered_free():
    """Peak 120 → recover to 110 must yield $0 fee and unchanged HWM."""
    from algochains_mcp.cloud_saas.realized_pnl import compute_hwm_performance_fee
    r = compute_hwm_performance_fee(prior_hwm=120.0, realized_pnl_to_date=110.0, rate=0.20)
    assert r["fee_base_usd"] == 0.0
    assert r["perf_fee_usd"] == 0.0
    assert r["new_hwm_usd"] == 120.0


def test_hwm_charges_only_new_profit_above_peak():
    from algochains_mcp.cloud_saas.realized_pnl import compute_hwm_performance_fee
    r = compute_hwm_performance_fee(prior_hwm=120.0, realized_pnl_to_date=150.0, rate=0.20)
    assert r["fee_base_usd"] == 30.0     # 150 - 120
    assert r["perf_fee_usd"] == 6.0      # 30 * 0.20
    assert r["new_hwm_usd"] == 144.0     # 150 - 6


def test_performance_fee_disabled_by_default():
    """Legal posture: perf fee must default to 0.0 (no charge) absent explicit enable."""
    from algochains_mcp.cloud_saas import realized_pnl
    importlib.reload(realized_pnl)
    assert realized_pnl.performance_fee_rate() == 0.0
    # With default rate, even a large new profit charges nothing.
    r = realized_pnl.compute_hwm_performance_fee(0.0, 1000.0)
    assert r["perf_fee_usd"] == 0.0


def test_performance_fee_rate_env_override(monkeypatch):
    from algochains_mcp.cloud_saas import realized_pnl
    monkeypatch.setenv("ALGOCHAINS_PERFORMANCE_FEE_RATE", "0.20")
    assert realized_pnl.performance_fee_rate() == 0.20


# ─── WS5: OAuth 2.1 resource-server validation ────────────────────────────────

def test_oauth_disabled_by_default_fails_closed(monkeypatch):
    monkeypatch.delenv("ALGOCHAINS_OAUTH_JWKS_URI", raising=False)
    monkeypatch.delenv("ALGOCHAINS_OAUTH_ENABLED", raising=False)
    from algochains_mcp.auth import oauth_resource
    assert oauth_resource.oauth_enabled() is False
    assert oauth_resource.validate_oauth_token("any.jwt.token") is None
    assert oauth_resource.validate_oauth_token(None) is None


def test_oauth_enabled_flag(monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_OAUTH_ENABLED", "true")
    from algochains_mcp.auth import oauth_resource
    assert oauth_resource.oauth_enabled() is True
    # A garbage token still fails closed (no JWKS / bad signature).
    assert oauth_resource.validate_oauth_token("garbage") is None


# ─── WS6: tenant isolation context ────────────────────────────────────────────

def test_tenant_context_set_get_require():
    from algochains_mcp.multi_tenant import isolation
    isolation.set_tenant("tenant-A")
    assert isolation.get_tenant() == "tenant-A"
    assert isolation.require_tenant() == "tenant-A"
    isolation.set_tenant(None)
    with pytest.raises(ValueError):
        isolation.require_tenant()


def test_tenant_context_manager_resets():
    from algochains_mcp.multi_tenant.isolation import TenantContext, get_tenant
    with TenantContext("t-ctx"):
        assert get_tenant() == "t-ctx"
    assert get_tenant() is None


# ─── Fail-closed DB-backed modules (no Supabase configured) ───────────────────

def test_referrals_fail_closed_without_db(monkeypatch):
    from algochains_mcp.cloud_saas import referrals
    monkeypatch.setattr(referrals, "_get_sb_client", lambda use_service_role=False: None, raising=False)
    r = referrals.get_my_referrals("sub_x")
    assert "error" in r


def test_usage_record_is_fail_open(monkeypatch):
    """record_usage must NEVER raise — metering can't block a tool call."""
    from algochains_mcp.cloud_saas import usage_metering
    # Force the client lookup to blow up; record_usage must still return cleanly.
    monkeypatch.setattr(
        usage_metering, "_get_sb_client",
        lambda use_service_role=False: (_ for _ in ()).throw(RuntimeError("boom")),
        raising=False,
    )
    out = usage_metering.record_usage("hash_x", "some_tool")
    assert out.get("recorded") is False


def test_included_quota_for_tier():
    from algochains_mcp.cloud_saas.usage_metering import included_quota_for_tier
    assert included_quota_for_tier("paper") == 1000
    assert included_quota_for_tier(None) == 1000  # safe default
    assert included_quota_for_tier("developer-live") == 10000


# ─── Tool registration invariants ─────────────────────────────────────────────

def test_new_tools_registered_and_money_tools_owner_only():
    from algochains_mcp.server import TOOLS, TIER1_TOOL_NAMES
    names = {t.name for t in TOOLS}
    tier1_tools = {
        "get_my_usage", "create_referral_code", "get_my_referrals",
        "get_referral_earnings", "get_my_realized_pnl", "accept_subscriber_terms",
    }
    for t in tier1_tools:
        assert t in names, f"{t} not registered in TOOLS"
        assert t in TIER1_TOOL_NAMES, f"{t} missing from TIER1"
    # Money / ledger movers must NEVER be in smart mode.
    for t in (
        "create_creator_onboarding_link",
        "get_my_creator_earnings",
        "run_creator_payouts",
        "reconcile_creator_pnl",
    ):
        assert t in names
        assert t not in TIER1_TOOL_NAMES, f"{t} must be owner-only, not TIER1"
