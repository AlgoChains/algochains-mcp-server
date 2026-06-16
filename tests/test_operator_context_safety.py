from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_operator_context_avoids_prompt_exfiltration_trigger_terms():
    operator_context = (REPO_ROOT / "MEGA_PROMPT_V22.md").read_text(encoding="utf-8")

    assert "system prompt" not in operator_context.lower()
    assert "no redaction" not in operator_context.lower()


def test_owner_heartbeat_docstring_avoids_reveal_signature():
    bridge_source = (REPO_ROOT / "src" / "algochains_mcp" / "http_bridge.py").read_text(
        encoding="utf-8"
    )

    assert "reveals infrastructure topology" not in bridge_source.lower()
