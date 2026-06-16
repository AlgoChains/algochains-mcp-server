from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]

OPERATOR_CONTEXT_SOURCES = [
    ROOT / "MEGA_PROMPT_V22.md",
    ROOT / "docs" / "ALGOCHAINS_MEGA_BLUEPRINT_V2.md",
    ROOT / "src" / "algochains_mcp" / "http_bridge.py",
]

SENSITIVE_PATTERNS = [
    re.compile("system" + r"\s+" + "prompt", re.IGNORECASE),
    re.compile("no" + r"\s+" + "redaction", re.IGNORECASE),
    re.compile("reveal" + r"\w*" + r"\s+" + "system" + r"\s+" + "prompt", re.IGNORECASE),
    re.compile("reveals" + r"\s+" + "infrastructure", re.IGNORECASE),
]


def test_operator_context_avoids_guardrail_trigger_wording() -> None:
    for source in OPERATOR_CONTEXT_SOURCES:
        text = source.read_text(encoding="utf-8")
        for pattern in SENSITIVE_PATTERNS:
            assert not pattern.search(text), f"{source.relative_to(ROOT)} contains {pattern.pattern}"
