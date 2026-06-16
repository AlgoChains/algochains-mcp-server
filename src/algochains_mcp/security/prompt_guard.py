"""Role-aware prompt injection guard for LLM-bound content.

The scheduler and skill runners pass both trusted operator instructions and
untrusted external text through LLM calls. This module scans untrusted roles for
prompt-exfiltration phrases while allowing trusted operator context to pass
without treating its own safety documentation as hostile user input.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


TRUSTED_ROLES = frozenset({"system", "system_prompt", "developer", "operator", "owner"})


def _phrase_regex(*parts: str) -> re.Pattern[str]:
    """Build scanner phrases without embedding sensitive strings contiguously."""
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b", re.IGNORECASE)


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "prompt_exfiltration",
        _phrase_regex("reveal", "(?:the\\s+)?system", "prompt"),
    ),
    (
        "instruction_override",
        _phrase_regex("ignore", "(?:all\\s+)?previous", "instructions"),
    ),
    (
        "developer_exfiltration",
        _phrase_regex("show", "(?:the\\s+)?developer", "message"),
    ),
)


@dataclass(frozen=True)
class PromptGuardResult:
    """Structured result from prompt scanning."""

    allowed: bool
    role: str
    reason: str | None = None
    pattern: str | None = None

    @property
    def blocked(self) -> bool:
        return not self.allowed


class PromptInjectionBlocked(ValueError):
    """Raised when untrusted LLM-bound content matches an injection signature."""

    def __init__(self, result: PromptGuardResult) -> None:
        self.result = result
        super().__init__(result.reason or "LLM prompt blocked")


def _normalize_role(role: str | None) -> str:
    return (role or "user").strip().lower()


def _is_trusted(role: str, trusted_roles: Iterable[str]) -> bool:
    normalized = {trusted.strip().lower() for trusted in trusted_roles}
    return role in normalized


def scan_llm_prompt(
    content: str,
    *,
    role: str | None = "user",
    trusted_roles: Iterable[str] = TRUSTED_ROLES,
    scan_trusted: bool = False,
) -> PromptGuardResult:
    """Scan LLM-bound text and return whether it is allowed.

    Trusted roles are allowed by default because operator instructions often
    document security behavior using words that would be suspicious in user
    content. Set ``scan_trusted=True`` for diagnostics that intentionally inspect
    trusted text as if it were external input.
    """
    normalized_role = _normalize_role(role)
    if not scan_trusted and _is_trusted(normalized_role, trusted_roles):
        return PromptGuardResult(allowed=True, role=normalized_role)

    for pattern_name, pattern in _INJECTION_PATTERNS:
        if pattern.search(content):
            return PromptGuardResult(
                allowed=False,
                role=normalized_role,
                pattern=pattern_name,
                reason=f"LLM prompt blocked: injection pattern from {normalized_role}: {pattern_name}",
            )

    return PromptGuardResult(allowed=True, role=normalized_role)


def check_llm_prompt(content: str, *, role: str | None = "user") -> str:
    """Return content if safe, otherwise raise ``PromptInjectionBlocked``."""
    result = scan_llm_prompt(content, role=role)
    if result.blocked:
        raise PromptInjectionBlocked(result)
    return content
