"""Prompt injection guard for LLM skill executors.

Addresses SAFE-MCP technique T094 (injection via tool output) and blocks
user/tool attempts to override operator-authored system instructions.

Operator/system prompts often *describe* attack phrases (e.g. "never reveal
system prompt") for defensive guidance. Scanning those trusted roles causes
false positives that trip skill circuit breakers (adaptive-brain,
crew-orchestrator, slack-command-listener, output-auditor, fat-finger-protection,
crew-handoff-router, chief-productivity-officer, cron-doctor).

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

# Labels that introduce operator-authored attack-phrase catalogs (Prohibited:/Examples:).
_CATALOG_HEADER_WORDS: frozenset[str] = frozenset(
    {
        "examples",
        "example",
        "injections",
        "injection",
        "patterns",
        "pattern",
        "attacks",
        "attack",
        "phrases",
        "phrase",
        "prohibited",
        "blocked",
        "banned",
        "forbidden",
        "deny",
        "denied",
        "monitor",
        "detect",
        "catalog",
        "security",
        "t094",
        "e",
        "g",
    }
)

# Single-token prefixes that indicate defensive guidance immediately before a match.
_DEFENSIVE_PREFIX_TOKENS: frozenset[str] = frozenset(
    {
        "never",
        "not",
        "no",
        "block",
        "blocked",
        "blocking",
        "prevent",
        "avoid",
        "stop",
        "refuse",
        "reject",
        "decline",
        "deny",
        "against",
        "without",
        "unless",
        "dont",
        "don't",
        "including",
        "include",
        "includes",
        "like",
        "say",
        "type",
        "e",
        "g",
        "examples",
        "example",
        "injections",
        "injection",
        "patterns",
        "pattern",
        "attacks",
        "attack",
        "phrases",
        "phrase",
        "prohibited",
        "banned",
        "forbidden",
        "or",
        "and",
        "cannot",
        "cant",
        "can't",
        "scan",
        "detect",
        "flag",
        "monitor",
    }
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
INJECTION_PATTERN_COUNT: int = len(INJECTION_PATTERNS)


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


def _line_before_match(text: str, match: re.Match[str]) -> str:
    line_start = text.rfind("\n", 0, match.start()) + 1
    return text[line_start : match.start()]


def _is_catalog_header_line(line_prefix: str) -> bool:
    """True when the current line begins with an operator catalog label."""
    if ":" not in line_prefix:
        return False
    header = line_prefix.split(":", 1)[0]
    if "t094" in header.lower():
        return True
    header_tokens = re.findall(r"[A-Za-z0-9']+", header)
    if not header_tokens:
        return False
    return header_tokens[-1].lower() in _CATALOG_HEADER_WORDS


def _is_defensive_catalog_context(text: str, match: re.Match[str]) -> bool:
    """True when a pattern appears inside operator defensive guidance, not an attack."""
    prefix = text[: match.start()]
    stripped_prefix = prefix.rstrip()
    if stripped_prefix.endswith(("(", "[", "/", "`", "'", '"', "-", "*", "•")):
        return True

    line_prefix = _line_before_match(text, match)
    if _is_catalog_header_line(line_prefix):
        return True

    if stripped_prefix.endswith(":"):
        doc_tokens = re.findall(r"[A-Za-z']+", prefix)
        if doc_tokens and doc_tokens[-1].lower() in _CATALOG_HEADER_WORDS:
            return True

    # Bullet item at line start: "- reveal system prompt"
    bullet_line = line_prefix.lstrip()
    if not bullet_line and match.start() > 0:
        return False
    if re.match(r"^[-*•]\s*$", bullet_line):
        return True

    tokens = re.findall(r"[A-Za-z']+", prefix)
    if not tokens:
        return False

    last = tokens[-1].lower()
    if last in _DEFENSIVE_PREFIX_TOKENS:
        return True
    if len(tokens) >= 2 and tokens[-2].lower() == "do" and last == "not":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() in {
        "requests",
        "request",
        "attempts",
        "attempt",
        "tries",
        "try",
        "asks",
        "ask",
        "asked",
        "told",
        "instructed",
    } and last == "to":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() in {
        "refuse",
        "reject",
        "decline",
    } and last == "to":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() in {"such", "for", "watch", "monitor"} and last == "as":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() in {
        "watch",
        "monitor",
        "scan",
        "detect",
        "flag",
    } and last == "for":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() == "e" and last == "g":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() == "may" and last in {
        "say",
        "type",
        "ask",
        "try",
        "attempt",
    }:
        return True
    if len(tokens) >= 2 and tokens[-2].lower() in {"users", "user"} and last == "may":
        return True
    return False


def find_injection_pattern(
    text: str,
    *,
    allow_catalog_context: bool = False,
) -> Optional[_InjectionPattern]:
    """Return the first injection pattern matched in *text*, if any."""
    if not text:
        return None
    for pattern in INJECTION_PATTERNS:
        for match in pattern.regex.finditer(text):
            if allow_catalog_context and _is_defensive_catalog_context(text, match):
                continue
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
    allow_catalog = normalized_role in TRUSTED_ROLES and scan_system
    matched = find_injection_pattern(body, allow_catalog_context=allow_catalog)
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
