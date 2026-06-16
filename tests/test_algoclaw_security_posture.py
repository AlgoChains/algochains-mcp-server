from __future__ import annotations

from algoclaw.cli import run_skill


def test_security_posture_reports_prompt_guard_active() -> None:
    result = run_skill("security-posture")

    assert "error" not in result
    assert result["result"]["prompt_guard"] == "active"
    assert result["result"]["replay_guard"] == "active"
    assert isinstance(result["result"]["replay_guard_nonce_count"], int)
