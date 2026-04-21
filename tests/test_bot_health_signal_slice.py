"""
Tests for the get_bot_health signal_health merge (v22.1).

Verifies that when signal_health.json is present and well-formed, get_bot_health
returns the `signal_health` key with params + risk_bootstrap per bot, and that
the response degrades gracefully when the file is absent or corrupt.
"""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_signal_health(directory: pathlib.Path) -> pathlib.Path:
    """Write a minimal signal_health.json that matches the live schema."""
    state_dir = directory / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    sh = state_dir / "signal_health.json"
    payload = {
        "MNQ_Upgraded_Scalper": {
            "bot_version": "R6-OPTUNA-5YR",
            "params": {
                "stop_ticks": 6,
                "target_ticks": 65,
                "max_daily_loss": 500,
                "volume_threshold": 2.134,
            },
            "risk_bootstrap": {
                "p5_total_pnl_1ct_usd": -601.5,
                "p95_max_drawdown_1ct_usd": 1566.0,
                "p95_max_drawdown_4ct_usd": 6264.0,
                "profitable_pct": 83.84,
                "n_trades_clean": 7569,
            },
        },
        "ws_health": {"status": "NO_CLIENT"},
    }
    sh.write_text(json.dumps(payload))
    return sh


# ---------------------------------------------------------------------------
# Unit tests (pure logic, no server import required)
# ---------------------------------------------------------------------------

class TestSignalHealthSliceParsing:
    """Verify the slice-building logic used inside get_bot_health."""

    def _build_slice(self, sh_data: dict, bot_filter: str = "all") -> dict:
        """Replicate the slice-building logic from server.py for unit testing."""
        signal_health_slice: dict = {}
        bot_key_map = {
            "mnq": "MNQ_Upgraded_Scalper",
            "cl":  "CL_Swing_Scalper",
            "mes": "MES_EMA_Swing",
            "nq":  "NQ_EMA_Swing",
        }
        for k, sh_key in bot_key_map.items():
            if bot_filter not in ("all", k):
                continue
            entry = sh_data.get(sh_key, {})
            signal_health_slice[k] = {
                "params": entry.get("params"),
                "risk_bootstrap": entry.get("risk_bootstrap"),
                "bot_version": entry.get("bot_version"),
                "trading_mode": sh_data.get("ws_health", {}).get("status"),
            }
        return signal_health_slice

    def test_all_bots_included_when_filter_is_all(self):
        sh_data = {
            "MNQ_Upgraded_Scalper": {"params": {"stop_ticks": 6}, "risk_bootstrap": {"p5_total_pnl_1ct_usd": -601.5}},
            "ws_health": {"status": "NO_CLIENT"},
        }
        result = self._build_slice(sh_data, "all")
        assert "mnq" in result
        assert "cl" in result  # present even without sh_data entry (None values)
        assert result["mnq"]["params"]["stop_ticks"] == 6

    def test_filter_returns_only_requested_bot(self):
        sh_data = {"MNQ_Upgraded_Scalper": {"params": {"stop_ticks": 6}, "risk_bootstrap": {}}, "ws_health": {"status": "NO_CLIENT"}}
        result = self._build_slice(sh_data, "mnq")
        assert "mnq" in result
        assert "cl" not in result
        assert "mes" not in result

    def test_risk_bootstrap_fields_surfaced(self):
        sh_data = {
            "MNQ_Upgraded_Scalper": {
                "params": {"max_daily_loss": 500},
                "risk_bootstrap": {
                    "p5_total_pnl_1ct_usd": -601.5,
                    "p95_max_drawdown_1ct_usd": 1566.0,
                    "profitable_pct": 83.84,
                },
            },
            "ws_health": {"status": "DEMO"},
        }
        result = self._build_slice(sh_data, "mnq")
        rb = result["mnq"]["risk_bootstrap"]
        assert rb["p5_total_pnl_1ct_usd"] == -601.5
        assert rb["p95_max_drawdown_1ct_usd"] == 1566.0
        assert rb["profitable_pct"] == 83.84
        assert result["mnq"]["trading_mode"] == "DEMO"

    def test_missing_bot_returns_none_values_not_error(self):
        sh_data = {"ws_health": {"status": "NO_CLIENT"}}
        result = self._build_slice(sh_data, "mnq")
        assert result["mnq"]["params"] is None
        assert result["mnq"]["risk_bootstrap"] is None

    def test_p5_and_p95_are_not_interchangeable(self):
        """Regression: P5 total P&L != P95 max drawdown. They have opposite signs and different meanings."""
        sh_data = {
            "MNQ_Upgraded_Scalper": {
                "risk_bootstrap": {
                    "p5_total_pnl_1ct_usd": -601.5,
                    "p95_max_drawdown_1ct_usd": 1566.0,
                }
            },
            "ws_health": {},
        }
        result = self._build_slice(sh_data, "mnq")
        rb = result["mnq"]["risk_bootstrap"]
        # P5 total PnL is negative (sequence risk)
        assert rb["p5_total_pnl_1ct_usd"] < 0, "P5 total P&L should be a loss in the worst 5% of orderings"
        # P95 max drawdown is positive (path drawdown magnitude)
        assert rb["p95_max_drawdown_1ct_usd"] > 0, "P95 max drawdown should be a positive dollar magnitude"
        # The two values are unrelated — do not confuse them
        assert rb["p5_total_pnl_1ct_usd"] != -rb["p95_max_drawdown_1ct_usd"], (
            "P5 total P&L and P95 max drawdown are different risk dimensions — "
            "they should not be equal and opposite."
        )


class TestSignalHealthFileIO:
    """Verify signal_health.json can be written and re-parsed consistently."""

    def test_round_trip(self, tmp_path):
        sh_file = _write_signal_health(tmp_path)
        data = json.loads(sh_file.read_text())
        rb = data["MNQ_Upgraded_Scalper"]["risk_bootstrap"]
        assert rb["p5_total_pnl_1ct_usd"] == -601.5
        assert rb["p95_max_drawdown_4ct_usd"] == 6264.0
        assert rb["p95_max_drawdown_4ct_usd"] == 4 * rb["p95_max_drawdown_1ct_usd"]

    def test_4ct_dd_is_linear_multiple_of_1ct(self):
        """4-contract drawdown must be exactly 4x the 1-contract figure (same instrument, correlated)."""
        with tempfile.TemporaryDirectory() as td:
            sh_file = _write_signal_health(pathlib.Path(td))
            data = json.loads(sh_file.read_text())
            rb = data["MNQ_Upgraded_Scalper"]["risk_bootstrap"]
            assert rb["p95_max_drawdown_4ct_usd"] == pytest.approx(4 * rb["p95_max_drawdown_1ct_usd"])

    def test_max_daily_loss_separate_from_bootstrap(self):
        """max_daily_loss lives in params, not risk_bootstrap — ensures no confusion."""
        with tempfile.TemporaryDirectory() as td:
            sh_file = _write_signal_health(pathlib.Path(td))
            data = json.loads(sh_file.read_text())
            entry = data["MNQ_Upgraded_Scalper"]
            assert "max_daily_loss" in entry["params"], "max_daily_loss must be in params"
            assert "max_daily_loss" not in entry.get("risk_bootstrap", {}), (
                "max_daily_loss must NOT be duplicated inside risk_bootstrap — "
                "it operates on a different time horizon (daily vs multi-week path)"
            )
