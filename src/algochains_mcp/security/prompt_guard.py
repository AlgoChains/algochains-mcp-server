"""LLM prompt injection guard for skill executors and agent runtimes.

Addresses SAFE-MCP T094 (injection via tool output) and false-positive blocks
when operator-authored system prompts include defensive rules such as
"never reveal the system prompt".

Control-tower / OpenClaw skill executors (e.g. ``generic_skill_executor.py``,
``slack-command-listener``) should import this module instead of maintaining a
local copy. Trusted ``system`` / ``system_prompt`` content is operator-authored
and is not scanned by default; only user/assistant/tool payloads are checked.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# Roles whose content is operator-authored (skill SKILL.md / platform config).
_TRUSTED_ROLES = frozenset({"system", "system_prompt", "developer"})

# User/assistant/tool roles that must be scanned before LLM dispatch.
_UNTRUSTED_ROLES = frozenset({"user", "human", "assistant", "tool", "function"})

# Named patterns surfaced in circuit-breaker / scheduler logs.
_INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "reveal system prompt": re.compile(
        r"\b(?:reveal|show|print|display|output|disclose|leak|dump)\b"
        r"(?:\s+\w+){0,4}\s+"
        r"(?:the\s+|your\s+)?(?:system\s+)?(?:prompt|instructions?)\b",
        re.IGNORECASE,
    ),
    "ignore previous instructions": re.compile(
        r"\bignore\b(?:\s+\w+){0,6}\s+\b(?:previous|prior|above|all)\b"
        r"(?:\s+\w+){0,4}\s+\b(?:instructions?|rules?|directives?)\b",
        re.IGNORECASE,
    ),
    "jailbreak mode": re.compile(
        r"\b(?:jailbreak|dan|developer\s+mode|god\s+mode)\b",
        re.IGNORECASE,
    ),
    "override safety": re.compile(
        r"\b(?:override|bypass|disable)\b(?:\s+\w+){0,4}\s+\b(?:safety|guardrails?|restrictions?)\b",
        re.IGNORECASE,
    ),
}

_DEFENSIVE_PREFIXES: tuple[str, ...] = (
    "never ",
    "do not ",
    "don't ",
    "dont ",
    "ignore ",
    "refuse ",
    "avoid ",
    "without ",
    "must not ",
    "should not ",
    "cannot ",
    "can't ",
)

# Strip obvious injection markers from untrusted tool output before agent context.
_TOOL_OUTPUT_STRIP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?im)^\s*system\s*:\s*.*$"),
    re.compile(r"(?im)^\s*assistant\s*:\s*ignore\b.*$"),
    re.compile(r"(?im)^\s*<\s*/?\s*system\s*>\s*$"),
)


@dataclass(frozen=True)
class PromptGuardResult:
    allowed: bool
    reason: str | None = None
    matched_pattern: str | None = None
    matched_role: str | None = None

    def as_error_message(self) -> str:
        if self.allowed:
            return ""
        role = self.matched_role or "prompt"
        pattern = self.matched_pattern or "unknown"
        return f"LLM prompt blocked: injection pattern from {role}: {pattern}"


def _scan_system_prompt_enabled() -> bool:
    return os.environ.get("ALGOCHAINS_PROMPT_GUARD_SCAN_SYSTEM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _normalize_role(role: str | None) -> str:
    return (role or "user").strip().lower().replace("-", "_")


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _find_injection(text: str) -> tuple[str, re.Pattern[str]] | None:
    if not text.strip():
        return None
    for name, pattern in _INJECTION_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        if name == "reveal system prompt" and _is_defensive_reveal_context(
            text, match.start()
        ):
            continue
        return name, pattern
    return None


def _is_defensive_reveal_context(text: str, match_start: int) -> bool:
    """Allow operator rules like 'never reveal the system prompt'."""
    prefix = text[:match_start].lower()
    window = prefix[-48:]
    return any(window.endswith(marker) or marker in window for marker in _DEFENSIVE_PREFIXES)


def check_llm_prompt(
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    messages: Sequence[Mapping[str, Any]] | None = None,
    scan_system_prompt: bool | None = None,
) -> PromptGuardResult:
    """Validate LLM-bound prompts. Blocks untrusted injection attempts only."""
    scan_system = (
        _scan_system_prompt_enabled()
        if scan_system_prompt is None
        else scan_system_prompt
    )

    structured: list[tuple[str, str]] = []
    if messages:
        for msg in messages:
            role = _normalize_role(msg.get("role"))
            text = _content_to_text(msg.get("content"))
            structured.append((role, text))
    else:
        if system_prompt:
            structured.append(("system_prompt", system_prompt))
        if user_prompt:
            structured.append(("user", user_prompt))

    for role, text in structured:
        if role in _TRUSTED_ROLES and not scan_system:
            continue
        if role not in _TRUSTED_ROLES and role not in _UNTRUSTED_ROLES:
            # Unknown roles fail closed unless explicitly trusted above.
            role = role or "user"
        match = _find_injection(text)
        if match:
            pattern_name, _ = match
            return PromptGuardResult(
                allowed=False,
                reason=f"LLM prompt blocked: injection pattern from {role}: {pattern_name}",
                matched_pattern=pattern_name,
                matched_role=role,
            )

    return PromptGuardResult(allowed=True)


def assert_llm_prompt_safe(
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    messages: Sequence[Mapping[str, Any]] | None = None,
    scan_system_prompt: bool | None = None,
) -> None:
    """Raise ValueError with scheduler-compatible message when blocked."""
    result = check_llm_prompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        messages=messages,
        scan_system_prompt=scan_system_prompt,
    )
    if not result.allowed:
        raise ValueError(result.as_error_message())


def sanitize_tool_output_for_context(text: str, *, max_length: int = 8000) -> str:
    """Remove common injection markers from untrusted tool/RAG output (T094)."""
    if not text:
        return ""
    cleaned = text
    for pattern in _TOOL_OUTPUT_STRIP_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3] + "..."
    return cleaned


def list_injection_patterns() -> list[str]:
    return list(_INJECTION_PATTERNS.keys())
