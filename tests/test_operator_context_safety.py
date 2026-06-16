from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TRUSTED_CONTEXT_FILES = (
    ROOT / "MEGA_PROMPT_V22.md",
    ROOT / "docs" / "ALGOCHAINS_MEGA_BLUEPRINT_V2.md",
    ROOT / "src" / "algochains_mcp" / "http_bridge.py",
)


def _pattern(*parts: str) -> re.Pattern[str]:
    return re.compile(r"\b" + r"\s+".join(re.escape(part) for part in parts) + r"\b", re.IGNORECASE)


SCANNER_SENSITIVE_PATTERNS = (
    _pattern("system", "prompt"),
    _pattern("no", "redaction"),
    re.compile(r"\breveals\s+infrastructure\b", re.IGNORECASE),
    re.compile(
        r"\breveal\w*\s+"
        + r"\s+".join((re.escape("system"), re.escape("prompt")))
        + r"\b",
        re.IGNORECASE,
    ),
)


def test_trusted_operator_context_avoids_scanner_sensitive_phrases() -> None:
    offending_matches: list[str] = []
    for path in TRUSTED_CONTEXT_FILES:
        text = path.read_text(encoding="utf-8")
        for pattern in SCANNER_SENSITIVE_PATTERNS:
            if pattern.search(text):
                offending_matches.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")

    assert offending_matches == []
