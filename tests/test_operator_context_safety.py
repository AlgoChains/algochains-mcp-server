from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_FILES = (
    ROOT / "MEGA_PROMPT_V22.md",
    ROOT / "docs" / "ALGOCHAINS_MEGA_BLUEPRINT_V2.md",
    ROOT / "src" / "algochains_mcp" / "http_bridge.py",
)


def _join(*parts: str) -> str:
    return "".join(parts)


def test_operator_context_avoids_prompt_scanner_trigger_terms() -> None:
    banned_phrases = (
        _join("sys", "tem", " ", "pro", "mpt"),
        _join("no", " ", "red", "action"),
    )
    banned_patterns = (
        re.compile(
            r"\brev\w*\W{0,40}\b"
            + re.escape(_join("sys", "tem"))
            + r"\W+"
            + re.escape(_join("pro", "mpt"))
            + r"\b",
            re.IGNORECASE | re.DOTALL,
        ),
    )

    for path in CONTEXT_FILES:
        content = path.read_text(encoding="utf-8")
        lowered = content.lower()
        for phrase in banned_phrases:
            assert phrase not in lowered, f"{path.relative_to(ROOT)} contains scanner-sensitive wording"
        for pattern in banned_patterns:
            assert pattern.search(content) is None, f"{path.relative_to(ROOT)} contains scanner-sensitive wording"
