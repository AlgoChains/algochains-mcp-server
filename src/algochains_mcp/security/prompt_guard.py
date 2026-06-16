"""Prompt injection guard for LLM skill executors.

Addresses SAFE-MCP technique T094 (injection via tool output) and blocks
user/tool attempts to override operator-authored system instructions.

Operator/system prompts often *describe* attack phrases (e.g. "never reveal
system prompt") for defensive guidance. Scanning those trusted roles causes
false positives that trip skill circuit breakers (crew-orchestrator,
slack-command-listener, output-auditor, fat-finger-protection).

By default, trusted roles (system, system_prompt, developer) are NOT scanned.
Set PROMPT_GUARD_SCAN_SYSTEM=1 to enforce scanning on every role.

Control-tower ``generic_skill_executor.py`` should import this module::

    from algochains_mcp.security.prompt_guard import check_llm_messages, format_block_message
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

# Roles authored by operators — not attacker-controlled at dispatch time.
TRUSTED_ROLES: frozenset[str] = frozenset({"system", "system_prompt", "developer"})

# Roles that must always be scanned even when scan_system is enabled for others.
UNTRUSTED_ROLES: frozenset[str] = frozenset(
    {"user", "human", "assistant", "tool", "function", "model"}
)


@dataclass(frozen=True)
class PromptBlock:
    """Structured result when an injection pattern matches."""

    role: str
    pattern_name: str
    matched_text: str


@dataclass(frozen=True)
class _InjectionPattern:
    name: str
    regex: re.Pattern[str]


def _compile_patterns() -> tuple[_InjectionPattern, ...]:
    raw: tuple[tuple[str, str], ...] = (
        ("reveal system prompt", r"\breveal\s+(?:the\s+)?system\s+prompt\b"),
        ("ignore previous instructions", r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions\b"),
        ("disregard system instructions", r"\bdisregard\s+(?:all\s+)?(?:system|your)\s+instructions\b"),
        ("you are now dan", r"\byou\s+are\s+now\s+dan\b"),
        ("jailbreak mode", r"\bjailbreak\s+mode\b"),
        ("developer mode enabled", r"\bdeveloper\s+mode\s+enabled\b"),
        ("repeat your instructions verbatim", r"\brepeat\s+(?:your\s+)?instructions\s+verbatim\b"),
        ("output initialization above", r"\boutput\s+(?:the\s+)?initialization\s+above\b"),
        ("new system prompt", r"\bnew\s+system\s+prompt\s*:"),
    )
    return tuple(
        _InjectionPattern(name=name, regex=re.compile(expr, re.IGNORECASE))
        for name, expr in raw
    )


INJECTION_PATTERNS: tuple[_InjectionPattern, ...] = _compile_patterns()


def scan_system_prompts_enabled() -> bool:
    """Return True when operator prompts should also be scanned."""
    return os.environ.get("PROMPT_GUARD_SCAN_SYSTEM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _normalize_role(role: Any) -> str:
    if role is None:
        return "user"
    return str(role).strip().lower()


def _normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _should_scan_role(role: str, *, scan_system: bool) -> bool:
    if role in TRUSTED_ROLES:
        return scan_system
    return True


def find_injection_pattern(text: str) -> Optional[_InjectionPattern]:
    """Return the first injection pattern matched in *text*, if any."""
    if not text:
        return None
    for pattern in INJECTION_PATTERNS:
        if pattern.regex.search(text):
            return pattern
    return None


def check_prompt_text(
    role: str,
    content: str,
    *,
    scan_system: Optional[bool] = None,
) -> Optional[PromptBlock]:
    """Scan a single message. Returns PromptBlock when blocked, else None."""
    normalized_role = _normalize_role(role)
    if scan_system is None:
        scan_system = scan_system_prompts_enabled()
    if not _should_scan_role(normalized_role, scan_system=scan_system):
        return None

    body = _normalize_content(content)
    matched = find_injection_pattern(body)
    if matched is None:
        return None

    match = matched.regex.search(body)
    matched_text = match.group(0) if match else matched.name
    return PromptBlock(
        role=normalized_role,
        pattern_name=matched.name,
        matched_text=matched_text,
    )


def check_llm_messages(
    messages: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    scan_system: Optional[bool] = None,
) -> Optional[PromptBlock]:
    """Scan an OpenAI-style message list. Returns first block hit, else None."""
    if scan_system is None:
        scan_system = scan_system_prompts_enabled()

    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = _normalize_role(message.get("role"))
        content = message.get("content", "")
        block = check_prompt_text(role, content, scan_system=scan_system)
        if block is not None:
            return block
    return None


def format_block_message(block: PromptBlock) -> str:
    """Format a block in the shape emitted by generic_skill_executor."""
    return (
        f"(LLM prompt blocked: injection pattern from {block.role}: "
        f"{block.pattern_name})"
    )


def validate_llm_messages(
    messages: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    *,
    scan_system: Optional[bool] = None,
) -> tuple[bool, Optional[str]]:
    """Return (allowed, block_message). block_message matches executor output."""
    block = check_llm_messages(messages, scan_system=scan_system)
    if block is None:
        return True, None
    return False, format_block_message(block)
