"""Prompt injection guard for LLM skill executors.

Scans untrusted message roles (user, tool, assistant from external sources)
for common prompt-injection patterns before they reach an LLM.

Operator-authored system prompts often include defensive phrases such as
"do not reveal system prompt". Scanning those trusted roles causes false
positives and trips skill circuit breakers (e.g. output-auditor,
slack-command-listener).

Control-tower skill executors should import this module rather than
maintaining a separate copy.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# Roles authored by operators / platform — not end-user input.
TRUSTED_ROLES: frozenset[str] = frozenset({"system", "system_prompt"})

# (pattern_name, compiled_regex) — pattern_name appears in block messages.
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "reveal system prompt",
        re.compile(r"\breveal\s+(?:your\s+)?(?:system\s+)?prompt\b", re.IGNORECASE),
    ),
    (
        "show system prompt",
        re.compile(r"\bshow\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?prompt\b", re.IGNORECASE),
    ),
    (
        "ignore previous instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "you are now",
        re.compile(r"\byou\s+are\s+now\s+(?:a\s+)?(?:different|new|another|evil|unrestricted)\b", re.IGNORECASE),
    ),
    (
        "developer mode",
        re.compile(r"\bdeveloper\s+mode\b", re.IGNORECASE),
    ),
    (
        "jailbreak",
        re.compile(r"\bjailbreak\b", re.IGNORECASE),
    ),
    (
        "prompt injection",
        re.compile(r"\bprompt\s+injection\b", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class PromptGuardResult:
    blocked: bool
    role: str | None = None
    pattern: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return not self.blocked


class PromptInjectionBlocked(ValueError):
    """Raised when validate_llm_prompt_or_raise rejects a message batch."""


def _env_scan_trusted_roles() -> bool:
    return os.environ.get("PROMPT_GUARD_SCAN_SYSTEM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _normalize_role(role: str | None) -> str:
    return (role or "user").strip().lower()


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        if "text" in content:
            return str(content.get("text") or "")
        return str(content)
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def find_injection_pattern(text: str) -> str | None:
    """Return the first matched injection pattern name, or None if clean."""
    if not text or not text.strip():
        return None
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return name
    return None


def scan_message(
    role: str,
    content: Any,
    *,
    scan_trusted_roles: bool | None = None,
) -> PromptGuardResult:
    """Scan one message. Trusted system roles are skipped by default."""
    normalized_role = _normalize_role(role)
    if scan_trusted_roles is None:
        scan_trusted_roles = _env_scan_trusted_roles()

    if not scan_trusted_roles and normalized_role in TRUSTED_ROLES:
        return PromptGuardResult(blocked=False, role=normalized_role)

    text = _extract_text(content)
    matched = find_injection_pattern(text)
    if matched:
        return PromptGuardResult(
            blocked=True,
            role=normalized_role,
            pattern=matched,
            message=(
                f"LLM prompt blocked: injection pattern from {normalized_role}: {matched}"
            ),
        )
    return PromptGuardResult(blocked=False, role=normalized_role)


def validate_llm_prompt(
    messages: Iterable[Mapping[str, Any]],
    *,
    scan_trusted_roles: bool | None = None,
) -> str | None:
    """Return a block reason string, or None when all messages are safe."""
    for message in messages:
        result = scan_message(
            str(message.get("role", "user")),
            message.get("content"),
            scan_trusted_roles=scan_trusted_roles,
        )
        if result.blocked:
            return result.message
    return None


def validate_llm_prompt_or_raise(
    messages: Iterable[Mapping[str, Any]],
    *,
    scan_trusted_roles: bool | None = None,
) -> None:
    """Fail closed on injection in untrusted roles."""
    reason = validate_llm_prompt(messages, scan_trusted_roles=scan_trusted_roles)
    if reason:
        raise PromptInjectionBlocked(reason)
