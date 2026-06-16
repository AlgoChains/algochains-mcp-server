"""Regression coverage for AlgoClaw security posture diagnostics."""
from __future__ import annotations

from algoclaw.cli import _run_security_posture


def test_security_posture_reports_prompt_guard_active() -> None:
    posture = _run_security_posture({})

    assert "error" not in posture
    assert posture["replay_guard"] == "active"
    assert posture["prompt_guard"] == "active"
    assert isinstance(posture["replay_guard_nonce_count"], int)
