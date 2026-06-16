"""Prevent trusted operator context from tripping external prompt scanners."""
from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]

OPERATOR_CONTEXT_FILES = (
    ROOT / "MEGA_PROMPT_V22.md",
    ROOT / "docs" / "ALGOCHAINS_MEGA_BLUEPRINT_V2.md",
    ROOT / "src" / "algochains_mcp" / "http_bridge.py",
)

BLOCKED_PATTERNS = (
    ("legacy operator heading", re.compile("system" + r"\s+" + "prompt", re.IGNORECASE)),
    ("legacy disclosure wording", re.compile("no" + r"\s+" + "redaction", re.IGNORECASE)),
    (
        "heartbeat topology wording",
        re.compile("reveals" + r"\s+" + "infrastructure", re.IGNORECASE),
    ),
    (
        "prompt exfiltration wording",
        re.compile("reveal\\w*" + r"\s+" + "system" + r"\s+" + "prompt", re.IGNORECASE),
    ),
)


def test_operator_context_avoids_prompt_scanner_trigger_phrases() -> None:
    offenders: list[str] = []
    for path in OPERATOR_CONTEXT_FILES:
        text = path.read_text(encoding="utf-8")
        for label, pattern in BLOCKED_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: {label}")

    assert offenders == []
