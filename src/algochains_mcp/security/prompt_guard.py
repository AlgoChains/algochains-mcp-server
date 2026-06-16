"""Prompt injection guard for LLM skill executors.

Addresses SAFE-MCP technique T094 (injection via tool output) and blocks
user/tool attempts to override operator-authored system instructions.

Operator/system prompts often *describe* attack phrases (e.g. "never reveal
system prompt") for defensive guidance. Scanning those trusted roles causes
false positives that trip skill circuit breakers (adaptive-brain,
crew-orchestrator, slack-command-listener, output-auditor, fat-finger-protection,
crew-handoff-router).

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


_COLON_CONTEXT_LABELS: frozenset[str] = frozenset(
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
        "blocked",
        "block",
        "policy",
        "security",
        "t094",
        "e",
        "g",
    }
)

_DEFENSIVE_PREFIX_WORDS: frozenset[str] = frozenset(
    {
        "never",
        "not",
        "no",
        "block",
        "blocked",
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
        "includes",
        "include",
        "like",
        "detect",
        "identify",
        "scan",
        "alert",
        "monitor",
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
    }
)


def _is_defensive_reveal_match(text: str, match: re.Match[str]) -> bool:
    """True when *reveal system prompt* appears in operator defensive guidance."""
    prefix = text[: match.start()]
    stripped_prefix = prefix.rstrip()
    if stripped_prefix.endswith(("(", "[", "/", "`")):
        return True
    if stripped_prefix.endswith(":"):
        doc_tokens = re.findall(r"[A-Za-z']+", prefix)
        if doc_tokens and doc_tokens[-1].lower() in _COLON_CONTEXT_LABELS:
            return True

    tokens = re.findall(r"[A-Za-z']+", prefix)
    if not tokens:
        return False

    last = tokens[-1].lower()
    if last in _DEFENSIVE_PREFIX_WORDS:
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
        "tried",
        "asks",
        "ask",
        "asked",
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
    if len(tokens) >= 2 and tokens[-2].lower() in {"watch", "monitor", "scan", "alert"} and last == "for":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() in {"watch", "monitor", "scan", "alert"} and last == "on":
        return True
    if len(tokens) >= 2 and tokens[-2].lower() == "e" and last == "g":
        return True
    if len(tokens) >= 3 and tokens[-3].lower() == "may" and tokens[-2].lower() in {"ask", "asks"}:
        return True
    if len(tokens) >= 2 and tokens[-2].lower() == "if" and last in {"asked", "requested"}:
        return True
    return False


def _is_imperative_reveal_match(text: str, match: re.Match[str]) -> bool:
    """True when *reveal system prompt* is an attacker-style imperative."""
    prefix = text[: match.start()].rstrip()
    suffix = text[match.end() :].lstrip()

    if not prefix:
        return True

    if re.search(r"\bfor\s+(?:debugging|audit|testing)\b[,:\s]*$", prefix, re.IGNORECASE):
        return True

    prefix_tokens = re.findall(r"[A-Za-z']+", prefix)
    if prefix_tokens:
        last = prefix_tokens[-1].lower()
        if last in {"please", "now", "immediately"}:
            return True

    if re.match(
        r"\bto\s+(?:the\s+)?(?:user|operator|them|me|slack)\b",
        suffix,
        re.IGNORECASE,
    ):
        return True

    return False


def find_injection_pattern(
    text: str,
    *,
    role: str = "user",
) -> Optional[_InjectionPattern]:
    """Return the first injection pattern matched in *text*, if any."""
    if not text:
        return None
    normalized_role = _normalize_role(role)
    trusted_role = normalized_role in TRUSTED_ROLES
    for pattern in INJECTION_PATTERNS:
        for match in pattern.regex.finditer(text):
            if pattern.name != "reveal system prompt":
                return pattern
            if _is_defensive_reveal_match(text, match):
                continue
            if trusted_role and not _is_imperative_reveal_match(text, match):
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
    matched = find_injection_pattern(body, role=normalized_role)
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
