"""Role-aware prompt-injection guard for LLM-bound text.

The scheduler and skill executor pass several text classes into model calls. Trusted
operator/developer context can legitimately describe guardrails and internal
architecture, while user and tool-sourced text must still be scanned aggressively.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


TRUSTED_ROLES = frozenset({
    "developer",
    "operator",
    "system",
    "system_prompt",
    "trusted_context",
})

UNTRUSTED_ROLES = frozenset({
    "assistant",
    "tool",
    "tool_output",
    "user",
})


@dataclass(frozen=True)
class PromptScanResult:
    """Structured result from prompt scanning."""

    allowed: bool
    role: str
    reason: str | None = None
    matched_pattern: str | None = None
    source: str | None = None


class PromptGuardViolation(ValueError):
    """Raised when untrusted LLM-bound text matches an injection signature."""

    def __init__(self, result: PromptScanResult) -> None:
        self.result = result
        detail = result.reason or "prompt_guard_violation"
        if result.matched_pattern:
            detail = f"{detail}: {result.matched_pattern}"
        super().__init__(detail)


def _phrase(*parts: str) -> str:
    return r"\s+".join(re.escape(part) for part in parts)


_INJECTION_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "prompt_exfiltration",
        re.compile(
            rf"\b(?:reveal|show|print|display|leak|dump)\s+(?:the\s+)?"
            rf"(?:{_phrase('system', 'prompt')}|{_phrase('system', 'message')}|"
            rf"{_phrase('developer', 'message')}|instructions?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        re.compile(
            rf"\b(?:ignore|bypass|override|forget)\s+(?:all\s+)?(?:previous\s+)?"
            rf"(?:instructions?|{_phrase('system', 'message')})\b",
            re.IGNORECASE,
        ),
    ),
)


def _normalize_role(role: str | None) -> str:
    return (role or "user").strip().lower().replace("-", "_")


def scan_llm_prompt(
    text: str,
    *,
    role: str | None = "user",
    source: str | None = None,
    scan_trusted_roles: bool = False,
) -> PromptScanResult:
    """Scan text before placing it into an LLM prompt.

    Trusted roles are allowed by default because repository operator context often
    discusses safety policy. Set ``scan_trusted_roles=True`` for audits that need to
    inspect trusted text without changing runtime behavior.
    """

    normalized_role = _normalize_role(role)
    if normalized_role in TRUSTED_ROLES and not scan_trusted_roles:
        return PromptScanResult(allowed=True, role=normalized_role, source=source)

    for reason, pattern in _INJECTION_SIGNATURES:
        if pattern.search(text):
            return PromptScanResult(
                allowed=False,
                role=normalized_role,
                reason=reason,
                matched_pattern=pattern.pattern,
                source=source,
            )

    return PromptScanResult(allowed=True, role=normalized_role, source=source)


def check_llm_prompt(
    text: str,
    *,
    role: str | None = "user",
    source: str | None = None,
    scan_trusted_roles: bool = False,
) -> str:
    """Return ``text`` if allowed, otherwise raise ``PromptGuardViolation``."""

    result = scan_llm_prompt(
        text,
        role=role,
        source=source,
        scan_trusted_roles=scan_trusted_roles,
    )
    if not result.allowed:
        raise PromptGuardViolation(result)
    return text
