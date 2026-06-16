"""Prompt injection guard for LLM skill executors.

Addresses SAFE-MCP technique T094 (injection via tool output) and blocks
user/tool attempts to override operator-authored system instructions.

Operator/system prompts often *describe* attack phrases (e.g. "never reveal
system prompt") for defensive guidance. Scanning those trusted roles causes
false positives that trip skill circuit breakers (adaptive-brain,
agent-orchestrator-v2, crew-orchestrator, slack-command-listener, output-auditor,
fat-finger-protection, crew-handoff-router, agent-knowledge-graph).

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

# Suffix of text immediately before a "reveal system prompt" match — defensive guidance.
_DEFENSIVE_REVEAL_PREFIX_TAIL = re.compile(
    r"(?:"
    r"\bif\s+(?:the\s+)?user\s+asks?\s+(?:you\s+)?to\s*$"
    r"|\bwhen\s+users?\s+asks?\s+(?:you\s+)?to\s*$"
    r"|\bif\s+asked\s+to\s*$"
    r"|\basks?\s+(?:you\s+)?to\s*$"
    r"|\b(?:cannot|can't|cant)\s*$"
    r"|\bunder\s+no\s+circumstances\s*$"
    r"|\b(?:monitor|watch)\s+for\s*$"
    r"|\b(?:include|includes|including)\s*$"
    r"|\bdo\s+not\s+comply\s+with\s*$"
    r"|\b(?:must|should|will)\s+not\s*$"
    r"|\bpass\s+through\s*$"
    r")",
    re.IGNORECASE,
)

_COLON_CONTEXT_TOKENS = frozenset(
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
        "policy",
        "security",
        "guard",
        "guardrail",
        "compliance",
        "blocked",
        "known",
        "such",
        "include",
        "content",
        "catalog",
        "t094",
        "e",
        "g",
    }
)

_DEFENSIVE_REVEAL_IMMEDIATE: frozenset[str] = frozenset(
    {
        "never",
        "not",
        "no",
        "block",
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
        "like",
        "for",
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
        "cannot",
        "cant",
        "circumstances",
    }
)

# Extra markers for operator system prompts (output-auditor, knowledge-graph, etc.).
_DEFENSIVE_REVEAL_OPERATOR_LOOKBACK: frozenset[str] = frozenset(
    {
        "treat",
        "regard",
        "classify",
        "consider",
        "label",
        "scan",
        "check",
        "search",
        "watch",
        "look",
        "monitor",
        "detect",
        "identify",
        "flag",
        "ignore",
        "mention",
        "describe",
        "contain",
        "containing",
        "contains",
        "include",
        "includes",
        "quote",
        "guard",
        "defend",
        "protect",
        "resist",
        "match",
        "reject",
        "if",
    }
)

_ASK_TO_TOKENS: frozenset[str] = frozenset(
    {
        "requests",
        "request",
        "attempts",
        "attempt",
        "tries",
        "try",
        "asks",
        "ask",
    }
)

_ASK_TO_LOOKBACK_TOKENS: frozenset[str] = frozenset(
    {
        "asked",
        "requested",
        "instructed",
        "directed",
    }
)

_REFUSE_TO_TOKENS: frozenset[str] = frozenset({"refuse", "reject", "decline", "deny"})

_SCAN_FOR_TOKENS: frozenset[str] = frozenset(
    {"scan", "check", "search", "watch", "look", "monitor"}
)

_TREAT_AS_TOKENS: frozenset[str] = frozenset(
    {"treat", "regard", "classify", "consider", "label"}
)


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


def _is_trusted_catalog_match(text: str, match: re.Match[str]) -> bool:
    """True when a pattern appears in an operator-authored example/catalog list."""
    prefix = text[: match.start()]
    stripped_prefix = prefix.rstrip()
    suffix = text[match.end() :]

    if stripped_prefix.endswith(("(", "[", "/")):
        return True
    if stripped_prefix.endswith("`") or suffix.lstrip().startswith("`"):
        return True
    if re.search(r"[\-*•]\s*$", stripped_prefix):
        return True
    if stripped_prefix.endswith(":"):
        doc_tokens = re.findall(r"[A-Za-z']+", prefix)
        if doc_tokens and doc_tokens[-1].lower() in _COLON_CONTEXT_TOKENS:
            return True
    return False


def _is_defensive_reveal_match(
    text: str,
    match: re.Match[str],
    *,
    trusted_context: bool = False,
) -> bool:
    """True when *reveal system prompt* appears in operator defensive guidance."""
    if trusted_context and _is_trusted_catalog_match(text, match):
        return True

    prefix = text[: match.start()]
    stripped_prefix = prefix.rstrip()
    suffix = text[match.end() :]

    if stripped_prefix.endswith(("(", "[", "/")):
        return True
    if trusted_context:
        if stripped_prefix.endswith("`") or suffix.lstrip().startswith("`"):
            return True
        if re.search(r"[\-*•]\s*$", stripped_prefix):
            return True
        if _DEFENSIVE_REVEAL_PREFIX_TAIL.search(prefix):
            return True
        if stripped_prefix.endswith(":"):
            doc_tokens = re.findall(r"[A-Za-z']+", prefix)
            if doc_tokens and doc_tokens[-1].lower() in _COLON_CONTEXT_TOKENS:
                return True

    tokens = re.findall(r"[A-Za-z']+", prefix)
    if not tokens:
        return False

    lowered = [token.lower() for token in tokens]
    last = lowered[-1]

    if last in _DEFENSIVE_REVEAL_IMMEDIATE:
        return True
    if len(lowered) >= 2 and lowered[-2] == "do" and last == "not":
        return True
    if len(lowered) >= 2 and lowered[-2] in _REFUSE_TO_TOKENS and last == "to":
        return True
    if len(lowered) >= 2 and lowered[-2] in {"such", "for", "watch", "monitor"} and last == "as":
        return True
    if len(lowered) >= 2 and lowered[-2] in _SCAN_FOR_TOKENS and last == "for":
        return True
    if len(lowered) >= 2 and lowered[-2] == "watch" and last == "for":
        return True
    if len(lowered) >= 2 and lowered[-2] == "e" and last == "g":
        return True
    if len(lowered) >= 3 and lowered[-3] in {"must", "should", "will"} and lowered[-2] == "not":
        return True

    if not trusted_context:
        return False

    if len(lowered) >= 2 and lowered[-2] in _ASK_TO_TOKENS and last == "to":
        return True
    if "ask" in set(lowered[-4:]) and last == "to":
        return True

    lookback = lowered[-10:]
    if last == "to" and any(token in _ASK_TO_TOKENS for token in lookback[:-1]):
        return True
    if last == "to" and any(token in _ASK_TO_LOOKBACK_TOKENS for token in lookback[:-1]):
        return True
    if last == "to" and any(token in _REFUSE_TO_TOKENS for token in lookback[:-1]):
        return True
    if last == "for" and any(token in _SCAN_FOR_TOKENS for token in lookback[:-1]):
        return True
    if last == "as" and any(token in _TREAT_AS_TOKENS for token in lookback[:-1]):
        return True
    if any(token in _DEFENSIVE_REVEAL_OPERATOR_LOOKBACK for token in lookback):
        return True
    return False


def find_injection_pattern(
    text: str,
    *,
    trusted_context: bool = False,
) -> Optional[_InjectionPattern]:
    """Return the first injection pattern matched in *text*, if any."""
    if not text:
        return None
    for pattern in INJECTION_PATTERNS:
        for match in pattern.regex.finditer(text):
            if trusted_context and _is_trusted_catalog_match(text, match):
                continue
            if pattern.name == "reveal system prompt" and _is_defensive_reveal_match(
                text, match, trusted_context=trusted_context
            ):
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
    trusted_context = normalized_role in TRUSTED_ROLES
    matched = find_injection_pattern(body, trusted_context=trusted_context)
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
