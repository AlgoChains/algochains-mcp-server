"""Prompt injection guard for LLM-bound content.

The guard is intentionally role-aware. Operator-authored system context often
contains defensive phrases such as "do not reveal system instructions"; those
phrases are safe when they come from trusted system/developer sources, but the
same strings must still be blocked when supplied by a user or tool output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


TRUSTED_PROMPT_ROLES = frozenset(
    {
        "system",
        "system_prompt",
        "developer",
        "developer_prompt",
        "operator",
        "operator_context",
        "trusted_system",
    }
)

DEFAULT_UNTRUSTED_ROLE = "user"


@dataclass(frozen=True)
class PromptGuardResult:
    """Structured scan result returned by the prompt guard."""

    allowed: bool
    role: str
    reason: str | None = None
    matched_text: str | None = None


class PromptInjectionBlocked(ValueError):
    """Raised when untrusted LLM-bound content matches an injection signature."""

    def __init__(self, result: PromptGuardResult) -> None:
        self.result = result
        detail = result.reason or "unknown"
        matched = f": {result.matched_text}" if result.matched_text else ""
        super().__init__(
            f"LLM prompt blocked: injection pattern from {result.role}: {detail}{matched}"
        )


_INJECTION_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "reveal_system_prompt",
        re.compile(
            r"\b(?:reveal|show|print|dump|disclose|exfiltrate|leak)\b"
            r"[\s\S]{0,80}\b(?:system|developer)\s+"
            r"(?:prompt|message|instructions?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|bypass|override|forget)\b"
            r"[\s\S]{0,80}\b(?:previous|prior|above|system|developer)\s+"
            r"(?:instructions?|prompt|message)\b",
            re.IGNORECASE,
        ),
    ),
)


def _normalize_role(role: str | None) -> str:
    normalized = (role or DEFAULT_UNTRUSTED_ROLE).strip().lower().replace("-", "_")
    return normalized or DEFAULT_UNTRUSTED_ROLE


def is_trusted_prompt_role(role: str | None) -> bool:
    """Return True when content is operator-authored system/developer context."""

    return _normalize_role(role) in TRUSTED_PROMPT_ROLES


def scan_llm_prompt(
    text: str,
    *,
    role: str | None = None,
    trusted: bool | None = None,
    extra_trusted_roles: Iterable[str] = (),
) -> PromptGuardResult:
    """Scan LLM-bound text for injection signatures.

    Trusted system/developer/operator context is allowed by default because it
    can legitimately discuss prompt-security rules. User, assistant, and tool
    content remain blocked on the same signatures.
    """

    normalized_role = _normalize_role(role)
    trusted_roles = TRUSTED_PROMPT_ROLES | frozenset(
        _normalize_role(extra_role) for extra_role in extra_trusted_roles
    )
    is_trusted = trusted if trusted is not None else normalized_role in trusted_roles
    if is_trusted:
        return PromptGuardResult(allowed=True, role=normalized_role)

    for reason, pattern in _INJECTION_SIGNATURES:
        match = pattern.search(text)
        if match:
            return PromptGuardResult(
                allowed=False,
                role=normalized_role,
                reason=reason,
                matched_text=match.group(0),
            )

    return PromptGuardResult(allowed=True, role=normalized_role)


def check_llm_prompt(
    text: str,
    *,
    role: str | None = None,
    trusted: bool | None = None,
    extra_trusted_roles: Iterable[str] = (),
    raise_on_block: bool = True,
) -> PromptGuardResult:
    """Validate LLM-bound text and optionally raise on unsafe input."""

    result = scan_llm_prompt(
        text,
        role=role,
        trusted=trusted,
        extra_trusted_roles=extra_trusted_roles,
    )
    if raise_on_block and not result.allowed:
        raise PromptInjectionBlocked(result)
    return result
