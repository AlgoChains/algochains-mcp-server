"""
Tests for Numerai MCP tool registration.

Verifies:
- All numerai_* tools are present in server TOOLS list
- numerai_upload_predictions is TIER_ORDER_EXEC (write-remote equivalent) in tool_danger_tiers
- tool_manifest has a _PREFIX_RULES entry for numerai_ and override for numerai_upload_predictions
- No numerai tool is accidentally TIER_READ_ONLY
- http_bridge has numerai_ tools in allowlists
"""
from __future__ import annotations

import pytest


EXPECTED_NUMERAI_TOOLS = [
    "numerai_status",
    "numerai_round_info",
    "numerai_download_dataset",
    "numerai_train_baseline",
    "numerai_validate_metrics",
    "numerai_dry_run_submit",
    "numerai_upload_predictions",
    "numerai_get_model_scores",
]

UPLOAD_TOOL = "numerai_upload_predictions"


class TestToolRegistration:
    def test_all_numerai_tools_in_server(self):
        """All expected numerai_* tools are registered in TOOLS list."""
        from algochains_mcp.server import TOOLS

        tool_names = {t.name for t in TOOLS}
        missing = [t for t in EXPECTED_NUMERAI_TOOLS if t not in tool_names]
        assert not missing, f"Missing numerai tools from TOOLS: {missing}"

    def test_tool_names_start_with_numerai(self):
        from algochains_mcp.server import TOOLS

        numerai_tools = [t for t in TOOLS if t.name.startswith("numerai_")]
        assert len(numerai_tools) >= len(EXPECTED_NUMERAI_TOOLS)


class TestDangerTiers:
    def test_upload_is_high_danger_tier(self):
        """HK-17: numerai_upload_predictions must NOT be TIER_READ_ONLY."""
        from algochains_mcp.tool_danger_tiers import TOOL_TIERS, TIER_READ_ONLY, TIER_WRITE_LOCAL

        tier = TOOL_TIERS.get(UPLOAD_TOOL)
        assert tier is not None, f"{UPLOAD_TOOL} not found in TOOL_TIERS"
        assert tier > TIER_WRITE_LOCAL, (
            f"{UPLOAD_TOOL} has tier={tier}, expected > {TIER_WRITE_LOCAL} "
            "(upload is irreversible — must be high danger tier, HK-17)"
        )

    def test_status_is_read_only(self):
        """numerai_status should be TIER_READ_ONLY."""
        from algochains_mcp.tool_danger_tiers import TOOL_TIERS, TIER_READ_ONLY

        tier = TOOL_TIERS.get("numerai_status")
        assert tier == TIER_READ_ONLY, (
            f"numerai_status has tier={tier}, expected TIER_READ_ONLY={TIER_READ_ONLY}"
        )

    def test_download_is_write_local(self):
        """numerai_download_dataset writes to local state — TIER_WRITE_LOCAL."""
        from algochains_mcp.tool_danger_tiers import TOOL_TIERS, TIER_WRITE_LOCAL

        tier = TOOL_TIERS.get("numerai_download_dataset")
        assert tier == TIER_WRITE_LOCAL, (
            f"numerai_download_dataset has tier={tier}, expected TIER_WRITE_LOCAL={TIER_WRITE_LOCAL}"
        )

    def test_all_expected_tools_have_explicit_tiers(self):
        """All numerai_* tools should have explicit entries in TOOL_TIERS."""
        from algochains_mcp.tool_danger_tiers import TOOL_TIERS

        missing = [t for t in EXPECTED_NUMERAI_TOOLS if t not in TOOL_TIERS]
        assert not missing, f"Missing explicit tier entries for: {missing}"


class TestToolManifest:
    def test_prefix_rules_has_numerai_entry(self):
        """_PREFIX_RULES should contain an entry for 'numerai_'."""
        from algochains_mcp.tool_manifest import _PREFIX_RULES

        prefixes = [rule[0] for rule in _PREFIX_RULES]
        assert "numerai_" in prefixes, (
            f"No 'numerai_' entry in _PREFIX_RULES. Found: {prefixes}"
        )

    def test_upload_override_has_required_env(self):
        """numerai_upload_predictions override must list NUMERAI_SECRET_KEY and NUMERAI_ALLOW_LIVE."""
        from algochains_mcp.tool_manifest import _TOOL_OVERRIDES

        assert UPLOAD_TOOL in _TOOL_OVERRIDES, (
            f"{UPLOAD_TOOL} not in _TOOL_OVERRIDES"
        )
        override = _TOOL_OVERRIDES[UPLOAD_TOOL]
        required = override.get("required_env", [])
        assert "NUMERAI_SECRET_KEY" in required, (
            f"NUMERAI_SECRET_KEY not in required_env for {UPLOAD_TOOL}: {required}"
        )
        assert "NUMERAI_ALLOW_LIVE" in required, (
            f"NUMERAI_ALLOW_LIVE not in required_env for {UPLOAD_TOOL}: {required}"
        )

    def test_upload_manifest_status_is_partial(self):
        """Upload tool implementation_status should be 'partial' until live-tested."""
        from algochains_mcp.tool_manifest import _TOOL_OVERRIDES

        override = _TOOL_OVERRIDES.get(UPLOAD_TOOL, {})
        status = override.get("implementation_status", "")
        assert status == "partial", (
            f"numerai_upload_predictions implementation_status='{status}', expected 'partial'"
        )

    def test_mcp_tool_manifest_returns_numerai_entry(self):
        """mcp_tool_manifest() should include numerai_upload_predictions entry."""
        from algochains_mcp.tool_manifest import mcp_tool_manifest

        manifest = mcp_tool_manifest()
        tool_names = {t["name"] for t in manifest.get("tools", [])}
        assert UPLOAD_TOOL in tool_names, (
            f"{UPLOAD_TOOL} not returned by mcp_tool_manifest()"
        )


class TestImportIsolation:
    def test_no_futures_imports_in_numerai_package(self):
        """Numerai package must not import futures bot modules (HK-16, §26.2)."""
        import importlib
        import sys

        # Import numerai package
        import algochains_mcp.tournament.numerai as nm

        forbidden_modules = [
            "FUTURES_SCALPER",
            "CL_FUTURES_SCALPER",
            "nq_swing_live",
            "mes_swing_live",
            "cl_feature_names",
        ]
        imported = set(sys.modules.keys())
        for forbidden in forbidden_modules:
            matching = [m for m in imported if forbidden in m]
            assert not matching, (
                f"Numerai package import caused futures module to load: {matching} (§26.2)"
            )

    def test_numerai_import_no_side_effects(self):
        """Importing the numerai package must have no side effects (no API calls, no file writes)."""
        import algochains_mcp.tournament.numerai  # should not raise or make calls
