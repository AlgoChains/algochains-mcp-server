from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_operator_context_avoids_prompt_exfiltration_guard_terms() -> None:
    """Trusted scheduler context should not trip naive prompt-injection filters."""
    text = (ROOT / "MEGA_PROMPT_V22.md").read_text(encoding="utf-8").lower()

    assert "system prompt" not in text
    assert "no redaction" not in text


def test_heartbeat_docstring_avoids_reveal_guard_term() -> None:
    text = (ROOT / "src/algochains_mcp/http_bridge.py").read_text(encoding="utf-8").lower()

    assert "reveals infrastructure topology" not in text
