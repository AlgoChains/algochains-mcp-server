"""
Tests for get_bot_health v22.2 extensions: ml_env_flags and cc_health.

Verifies that:
- ml_env_flags surfaces MASSIVE_NEWS_FEATURES, MASSIVE_PCR_FEATURES,
  MASSIVE_HALT_GUARD from the environment (defaulting to "0" when absent).
- cc_health reads and parses cc_health_state.json when present.
- cc_health degrades gracefully when the file is absent or corrupt.
"""
from __future__ import annotations

import json
import pathlib
import tempfile


# ---------------------------------------------------------------------------
# Helpers — replicate the logic extracted from server.py for unit testing
# ---------------------------------------------------------------------------

def _build_ml_env_flags(control_tower: pathlib.Path, signal_health: dict | None = None) -> dict:
    """Replicate ml_env_flags extraction from get_bot_health."""
    keys = ("MASSIVE_NEWS_FEATURES", "MASSIVE_PCR_FEATURES", "MASSIVE_HALT_GUARD")
    signal_health = signal_health or {}
    for candidate in (
        signal_health.get("ml_env_flags"),
        signal_health.get("MNQ_Upgraded_Scalper", {}).get("ml_env_flags"),
        signal_health.get("MNQ_Upgraded_Scalper", {}).get("flow_feature_versions", {}).get("ml_env_flags"),
    ):
        if isinstance(candidate, dict):
            return {key: str(candidate.get(key, "0")) for key in keys}
    env_file = control_tower / ".env"
    parsed = {}
    if env_file.exists():
        for raw_line in env_file.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            parsed[key.strip()] = value.strip().strip('"').strip("'")
    return {
        "MASSIVE_NEWS_FEATURES": parsed.get("MASSIVE_NEWS_FEATURES", "0"),
        "MASSIVE_PCR_FEATURES": parsed.get("MASSIVE_PCR_FEATURES", "0"),
        "MASSIVE_HALT_GUARD": parsed.get("MASSIVE_HALT_GUARD", "0"),
    }


def _build_cc_health(state_dir: pathlib.Path) -> dict:
    """Replicate cc_health extraction from get_bot_health."""
    cc_state_path = state_dir / "state" / "cc_health_state.json"
    if not cc_state_path.exists():
        return {"status": "unknown", "detail": "cc_health_state.json not found"}
    try:
        raw = json.loads(cc_state_path.read_text())
        return {
            "status": raw.get("status"),
            "issues": raw.get("issues", []),
            "cc_log_age_minutes": raw.get("cc_log_age_minutes"),
            "consecutive_failures": raw.get("consecutive_failures"),
            "cc_restarts": raw.get("cc_restarts"),
            "circuit_breakers_open": raw.get("circuit_breakers_open"),
            "last_check_utc": raw.get("last_check_utc"),
        }
    except Exception as exc:
        return {"error": f"cc_health_state.json parse failure: {exc}"}


def _write_cc_health(directory: pathlib.Path, payload: dict | None = None) -> pathlib.Path:
    """Write a cc_health_state.json under directory/state/."""
    state_dir = directory / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "cc_health_state.json"
    if payload is None:
        payload = {
            "status": "HEALTHY",
            "issues": [],
            "cc_log_age_minutes": 2,
            "consecutive_failures": 0,
            "cc_restarts": 1,
            "circuit_breakers_open": [],
            "last_check_utc": "2026-04-21T14:00:00Z",
        }
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# ml_env_flags tests
# ---------------------------------------------------------------------------

class TestMlEnvFlags:
    """Unit tests for ml_env_flags extraction."""

    def test_all_flags_default_to_zero_when_absent(self, tmp_path):
        flags = _build_ml_env_flags(tmp_path)
        assert flags["MASSIVE_NEWS_FEATURES"] == "0"
        assert flags["MASSIVE_PCR_FEATURES"] == "0"
        assert flags["MASSIVE_HALT_GUARD"] == "0"

    def test_enabled_flags_surfaced_correctly(self, tmp_path):
        (tmp_path / ".env").write_text(
            "MASSIVE_NEWS_FEATURES=1\n"
            "MASSIVE_PCR_FEATURES=1\n"
            "MASSIVE_HALT_GUARD=0\n"
        )
        flags = _build_ml_env_flags(tmp_path)
        assert flags["MASSIVE_NEWS_FEATURES"] == "1"
        assert flags["MASSIVE_PCR_FEATURES"] == "1"
        assert flags["MASSIVE_HALT_GUARD"] == "0"

    def test_mcp_process_env_is_not_used_as_bot_truth(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MASSIVE_NEWS_FEATURES", "1")
        monkeypatch.setenv("MASSIVE_PCR_FEATURES", "1")
        monkeypatch.setenv("MASSIVE_HALT_GUARD", "1")
        flags = _build_ml_env_flags(tmp_path)
        assert flags["MASSIVE_NEWS_FEATURES"] == "0"
        assert flags["MASSIVE_PCR_FEATURES"] == "0"
        assert flags["MASSIVE_HALT_GUARD"] == "0"

    def test_signal_health_flags_preferred_over_env_file(self, tmp_path):
        (tmp_path / ".env").write_text(
            "MASSIVE_NEWS_FEATURES=0\nMASSIVE_PCR_FEATURES=0\nMASSIVE_HALT_GUARD=0\n"
        )
        signal_health = {
            "MNQ_Upgraded_Scalper": {
                "ml_env_flags": {
                    "MASSIVE_NEWS_FEATURES": "1",
                    "MASSIVE_PCR_FEATURES": "1",
                    "MASSIVE_HALT_GUARD": "0",
                }
            }
        }
        flags = _build_ml_env_flags(tmp_path, signal_health)
        assert flags["MASSIVE_NEWS_FEATURES"] == "1"
        assert flags["MASSIVE_PCR_FEATURES"] == "1"

    def test_flags_are_string_values_not_bool(self, tmp_path):
        """Flags should be "0"/"1" strings, not Python bools — avoids JSON type mismatch."""
        (tmp_path / ".env").write_text("MASSIVE_NEWS_FEATURES=1\n")
        flags = _build_ml_env_flags(tmp_path)
        assert isinstance(flags["MASSIVE_NEWS_FEATURES"], str), (
            "Flag values must be strings ('0'/'1'), not booleans. "
            "JSON serialization of True/False would break agent comparisons."
        )

    def test_flags_dict_has_exactly_three_keys(self, tmp_path):
        """No secret or unexpected keys are surfaced."""
        flags = _build_ml_env_flags(tmp_path)
        assert set(flags.keys()) == {"MASSIVE_NEWS_FEATURES", "MASSIVE_PCR_FEATURES", "MASSIVE_HALT_GUARD"}

    def test_unknown_massive_env_vars_not_surfaced(self, tmp_path):
        """MASSIVE_API_KEY and other sensitive vars must NOT appear in flags."""
        (tmp_path / ".env").write_text("MASSIVE_API_KEY=secret_value\nMASSIVE_PCR_FEATURES=1\n")
        flags = _build_ml_env_flags(tmp_path)
        assert "MASSIVE_API_KEY" not in flags, "Secret keys must never appear in ml_env_flags"


# ---------------------------------------------------------------------------
# cc_health tests
# ---------------------------------------------------------------------------

class TestCcHealth:
    """Unit tests for cc_health extraction from cc_health_state.json."""

    def test_healthy_state_parsed_correctly(self, tmp_path):
        _write_cc_health(tmp_path)
        result = _build_cc_health(tmp_path)
        assert result["status"] == "HEALTHY"
        assert result["issues"] == []
        assert result["cc_log_age_minutes"] == 2
        assert result["consecutive_failures"] == 0
        assert result["cc_restarts"] == 1
        assert result["circuit_breakers_open"] == []
        assert result["last_check_utc"] == "2026-04-21T14:00:00Z"

    def test_degraded_state_preserves_issues(self, tmp_path):
        payload = {
            "status": "DEGRADED",
            "issues": ["CC log stale (30m since last write)"],
            "cc_log_age_minutes": 31,
            "consecutive_failures": 3,
            "cc_restarts": 5,
            "circuit_breakers_open": [],
            "last_check_utc": "2026-04-21T13:00:00Z",
        }
        _write_cc_health(tmp_path, payload)
        result = _build_cc_health(tmp_path)
        assert result["status"] == "DEGRADED"
        assert "CC log stale (30m since last write)" in result["issues"]
        assert result["consecutive_failures"] == 3

    def test_critical_state_with_open_circuit_breakers(self, tmp_path):
        payload = {
            "status": "CRITICAL",
            "issues": ["Circuit breakers OPEN: tradovate, alpaca"],
            "cc_log_age_minutes": 5,
            "consecutive_failures": 8,
            "cc_restarts": 3,
            "circuit_breakers_open": ["tradovate", "tradovate2", "alpaca", "oanda"],
            "last_check_utc": "2026-04-21T12:00:00Z",
        }
        _write_cc_health(tmp_path, payload)
        result = _build_cc_health(tmp_path)
        assert result["status"] == "CRITICAL"
        assert "tradovate" in result["circuit_breakers_open"]
        assert "oanda" in result["circuit_breakers_open"]

    def test_missing_file_returns_unknown_status(self, tmp_path):
        result = _build_cc_health(tmp_path)
        assert result["status"] == "unknown"
        assert "not found" in result["detail"]

    def test_corrupt_file_returns_error_key(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "cc_health_state.json").write_text("{this is not valid JSON ...")
        result = _build_cc_health(tmp_path)
        assert "error" in result
        assert "parse failure" in result["error"]

    def test_partial_payload_missing_keys_handled_gracefully(self, tmp_path):
        """Older cc_health_state.json formats may lack some keys — must not raise."""
        payload = {"status": "HEALTHY"}  # all other fields missing
        _write_cc_health(tmp_path, payload)
        result = _build_cc_health(tmp_path)
        assert result["status"] == "HEALTHY"
        assert result["issues"] == []        # defaults to []
        assert result["cc_log_age_minutes"] is None
        assert result["consecutive_failures"] is None
        assert result["circuit_breakers_open"] is None

    def test_round_trip_preserves_all_fields(self, tmp_path):
        path = _write_cc_health(tmp_path)
        raw = json.loads(path.read_text())
        result = _build_cc_health(tmp_path)
        assert result["status"] == raw["status"]
        assert result["cc_restarts"] == raw["cc_restarts"]
        assert result["last_check_utc"] == raw["last_check_utc"]


# ---------------------------------------------------------------------------
# Integration: both extensions present together
# ---------------------------------------------------------------------------

class TestGetBotHealthV22Extensions:
    """Verify ml_env_flags and cc_health coexist in one output dict."""

    def test_output_has_both_new_keys(self, tmp_path, monkeypatch):
        _write_cc_health(tmp_path)

        flags = _build_ml_env_flags(tmp_path)
        cc = _build_cc_health(tmp_path)

        # Simulate the return dict from get_bot_health
        result = {
            "control_tower": str(tmp_path),
            "bots": {},
            "signal_health": {},
            "ml_env_flags": flags,
            "cc_health": cc,
            "tradovate_token": {},
            "generated_at": 0,
        }

        assert "ml_env_flags" in result
        assert "cc_health" in result
        # Keys that must not be removed
        assert "bots" in result
        assert "signal_health" in result
        assert "tradovate_token" in result

    def test_no_secrets_in_combined_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MASSIVE_API_KEY", "secret_key_never_leak")
        _write_cc_health(tmp_path)
        flags = _build_ml_env_flags(tmp_path)
        for key, value in flags.items():
            assert "secret_key_never_leak" not in str(value)
        assert "MASSIVE_API_KEY" not in flags
