"""Shared MCP tool policy for transports and dynamic dispatch.

This module is deliberately small and side-effect free so stdio MCP, the HTTP
bridge, CLI diagnostics, and future local bridges can agree on the same danger
tier and approval vocabulary.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

from .tool_danger_tiers import (
    TIER_DESTRUCTIVE,
    TIER_ORDER_EXEC,
    get_danger_tier,
    get_danger_tier_source,
    get_scope_max_tier,
    get_tier_label,
)
from .otel_tracing import redacted_argument_hash


TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP_BRIDGE = "http_bridge"
TRANSPORT_DYNAMIC = "dynamic"
TRANSPORT_LOCAL_MCP = "local_mcp"


@dataclass(frozen=True)
class ToolPolicyDecision:
    allow: bool
    tool: str
    transport: str
    danger_tier: int
    danger_label: str
    tier_source: str
    required_scope: str | None = None
    required_arg: str | None = None
    required_secret: str | None = None
    reason: str = ""

    def as_error(self) -> dict[str, Any]:
        return {
            "error": self.reason or "Tool policy denied execution",
            "blocked": True,
            "tool": self.tool,
            "transport": self.transport,
            "danger_tier": self.danger_tier,
            "danger_label": self.danger_label,
            "tier_source": self.tier_source,
            "required_scope": self.required_scope,
            "required_arg": self.required_arg,
            "required_secret": self.required_secret,
        }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "confirm", "confirmed"}
    return False


def has_confirm(arguments: dict[str, Any] | None) -> bool:
    """Normalize the approval argument for new and legacy callers.

    `confirm` is the canonical shape. `confirmed` remains accepted as a legacy
    alias so older tools can be migrated without adding a third vocabulary.
    """
    args = arguments or {}
    return _truthy(args.get("confirm")) or _truthy(args.get("confirmed"))


def approval_shape_for_tier(tier: int, *, transport: str) -> dict[str, Any]:
    if tier >= TIER_ORDER_EXEC:
        return {
            "requires_confirmation": True,
            "canonical_arg": "confirm=true",
            "legacy_aliases": ["confirmed=true"],
            "requires_owner_secret": transport in {TRANSPORT_HTTP_BRIDGE, TRANSPORT_DYNAMIC},
        }
    return {
        "requires_confirmation": False,
        "canonical_arg": None,
        "legacy_aliases": [],
        "requires_owner_secret": False,
    }


def approval_shape(tool_name: str, *, transport: str) -> dict[str, Any]:
    return approval_shape_for_tier(get_danger_tier(tool_name), transport=transport)


def explain_decision(
    decision: ToolPolicyDecision,
    *,
    arguments: dict[str, Any] | None = None,
    transports_allowed: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Return a redacted, operator-facing explanation for a policy decision."""
    reasons = []
    if decision.reason:
        reasons.append(decision.reason)
    if decision.required_scope:
        reasons.append(f"Requires caller scope: {decision.required_scope}")
    if decision.required_arg:
        reasons.append(f"Requires approval argument: {decision.required_arg}")
    if decision.required_secret:
        reasons.append(f"Requires secret: {decision.required_secret}")
    if not reasons and decision.allow:
        reasons.append("Allowed by shared AlgoChains tool policy.")

    return {
        "ok": decision.allow,
        "decision": "allow" if decision.allow else "deny",
        "tool": decision.tool,
        "transport": decision.transport,
        "danger_tier": decision.danger_tier,
        "danger_label": decision.danger_label,
        "tier_source": decision.tier_source,
        "required_scope": decision.required_scope,
        "required_arg": decision.required_arg,
        "required_secret": decision.required_secret,
        "approval_shape": approval_shape_for_tier(
            decision.danger_tier,
            transport=decision.transport,
        ),
        "argument_hash": redacted_argument_hash(arguments or {}),
        "arguments_redacted": True,
        "transports_allowed": transports_allowed or {},
        "reasons": reasons,
    }


def required_scope_for_tier(tier: int) -> str | None:
    if tier >= TIER_DESTRUCTIVE:
        return "admin"
    if tier >= TIER_ORDER_EXEC:
        return "interactive"
    return None


def visible_tools_for_bridge(
    *,
    public_tools: Iterable[str],
    owner_tools: Iterable[str],
    is_owner: bool,
    caller_scope: str | None,
) -> list[str]:
    visible = set(public_tools)
    if is_owner:
        max_tier = get_scope_max_tier(caller_scope)
        visible.update(tool for tool in owner_tools if get_danger_tier(tool) <= max_tier)
    return sorted(visible)


def evaluate_bridge_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    is_owner: bool,
    caller_scope: str | None,
    public_tools: set[str],
    owner_tools: set[str],
) -> ToolPolicyDecision:
    tier = get_danger_tier(tool_name)
    label = get_tier_label(tier)
    source = get_danger_tier_source(tool_name)

    base = dict(
        tool=tool_name,
        transport=TRANSPORT_HTTP_BRIDGE,
        danger_tier=tier,
        danger_label=label,
        tier_source=source,
    )

    if tool_name in owner_tools and not is_owner:
        return ToolPolicyDecision(
            False,
            **base,
            required_secret="ALGOCHAINS_BRIDGE_API_KEY",
            reason="Unauthorized — this tool requires owner bridge access.",
        )
    if tool_name not in public_tools and tool_name not in owner_tools:
        return ToolPolicyDecision(
            False,
            **base,
            reason=f"Tool '{tool_name}' is not available via the HTTP bridge.",
        )
    if not is_owner:
        return ToolPolicyDecision(True, **base)

    scope_max_tier = get_scope_max_tier(caller_scope)
    if scope_max_tier < tier:
        required_scope = required_scope_for_tier(tier)
        return ToolPolicyDecision(
            False,
            **base,
            required_scope=required_scope,
            reason=(
                f"Caller scope '{caller_scope}' is limited to danger tier {scope_max_tier}; "
                f"tool '{tool_name}' requires tier {tier}."
            ),
        )
    if tier >= TIER_ORDER_EXEC and not has_confirm(arguments):
        return ToolPolicyDecision(
            False,
            **base,
            required_scope=required_scope_for_tier(tier),
            required_arg="confirm=true",
            reason=(
                f"Tool '{tool_name}' has danger tier {label} ({tier}). "
                "Pass confirm=true in arguments to execute."
            ),
        )

    # Opt-in: ALGOCHAINS_BRIDGE_REQUIRE_OWNER_TOKEN=1 requires owner_token in
    # arguments for ORDER_EXEC+ tools. Off by default — warn-only until frontend
    # clients have been updated to pass the token.
    if tier >= TIER_ORDER_EXEC and os.environ.get("ALGOCHAINS_BRIDGE_REQUIRE_OWNER_TOKEN", "0") == "1":
        expected = os.environ.get("OWNER_API_TOKEN", "")
        provided = (arguments or {}).get("owner_token", "")
        if not expected or provided != expected:
            import logging
            logging.getLogger("algochains_mcp.tool_policy").warning(
                "[bridge] ORDER_EXEC tool '%s' called without matching owner_token "
                "(ALGOCHAINS_BRIDGE_REQUIRE_OWNER_TOKEN=1 is active). "
                "Pass owner_token in arguments.",
                tool_name,
            )
            return ToolPolicyDecision(
                False,
                **base,
                required_secret="OWNER_API_TOKEN",
                reason=(
                    f"Tool '{tool_name}' requires owner_token in arguments "
                    "(ALGOCHAINS_BRIDGE_REQUIRE_OWNER_TOKEN=1). "
                    "Pass matching OWNER_API_TOKEN value as owner_token."
                ),
            )

    return ToolPolicyDecision(True, **base)


def evaluate_dynamic_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    expected_owner_token: str,
) -> ToolPolicyDecision:
    tier = get_danger_tier(tool_name)
    label = get_tier_label(tier)
    source = get_danger_tier_source(tool_name)
    base = dict(
        tool=tool_name,
        transport=TRANSPORT_DYNAMIC,
        danger_tier=tier,
        danger_label=label,
        tier_source=source,
    )
    if tier < TIER_ORDER_EXEC:
        # Sensitive sub-ORDER tools are gated behind owner_token in dynamic
        # dispatch even though their tiers are below ORDER_EXEC. This catches
        # credential writers plus service-role or ambient-credential reads/writes
        # whose names otherwise match broad READ_ONLY/WRITE_LOCAL prefix rules.
        _SENSITIVE_SUB_ORDER_TOOLS = frozenset({
            "provision_key",
            "store_api_key",
            "rotate_api_key",
            "set_byok_key",
            "get_subscriber_bots",
            "get_user_bot_metrics",
            "get_all_user_bots",
            "revoke_broker_connection",
            "submit_to_marketplace",
        })
        if tool_name in _SENSITIVE_SUB_ORDER_TOOLS:
            provided_tok = (arguments or {}).get("owner_token", "")
            # Fail-closed: if OWNER_API_TOKEN is not configured, deny credential-writing
            # tools entirely (mirrors ORDER_EXEC behavior). Allowing them when the token
            # is unset would let any stdio attacker write to .env / key store.
            if not expected_owner_token or provided_tok != expected_owner_token:
                return ToolPolicyDecision(
                    False,
                    **base,
                    required_secret="OWNER_API_TOKEN",
                    reason=(
                        f"execute_dynamic_tool: '{tool_name}' accesses sensitive "
                        "operator-scoped state and requires owner_token authorization "
                        "even below ORDER_EXEC tier. "
                        "Set OWNER_API_TOKEN in .env and pass a matching owner_token "
                        "inside the 'arguments' payload."
                    ),
                )
        return ToolPolicyDecision(True, **base)

    provided = (arguments or {}).get("owner_token", "")
    if not expected_owner_token or provided != expected_owner_token:
        return ToolPolicyDecision(
            False,
            **base,
            required_secret="OWNER_API_TOKEN",
            reason=(
                f"execute_dynamic_tool: '{tool_name}' requires {label} authorization. "
                "Pass a matching owner_token inside the 'arguments' payload."
            ),
        )
    if not has_confirm(arguments):
        return ToolPolicyDecision(
            False,
            **base,
            required_arg="confirm=true",
            required_secret="OWNER_API_TOKEN",
            reason=(
                f"execute_dynamic_tool: '{tool_name}' requires explicit confirm=true "
                f"for {label} dynamic execution."
            ),
        )
    return ToolPolicyDecision(True, **base)


def evaluate_stdio_direct_tool(
    tool_name: str,
    *,
    tool_mode: str,
    tier1_names: set[str],
    owner_token: str | None = None,
    require_confirmation: bool = True,
) -> ToolPolicyDecision:
    """Evaluate whether a direct stdio tool call is permitted.

    ALGOCHAINS_TOOL_MODE=full is DEVELOPMENT/DEBUG ONLY.
    In production, smart mode routes ORDER_EXEC+ tools through execute_dynamic_tool
    which enforces owner_token + confirm=true. Full mode exposes all tools for direct
    call but must still enforce the same ORDER_EXEC gate for trading/destructive tools.

    Args:
        tool_name: The MCP tool being invoked.
        tool_mode: "smart" or "full" from config.
        tier1_names: Set of tools always directly callable in smart mode.
        owner_token: Caller's owner_token argument (for full-mode ORDER_EXEC check).
        require_confirmation: Value of ALGOCHAINS_REQUIRE_CONFIRMATION env (default True).
    """
    tier = get_danger_tier(tool_name)
    label = get_tier_label(tier)
    base = dict(
        tool=tool_name,
        transport=TRANSPORT_STDIO,
        danger_tier=tier,
        danger_label=label,
        tier_source=get_danger_tier_source(tool_name),
    )

    if tool_mode != "full" and tool_name not in tier1_names:
        return ToolPolicyDecision(
            False,
            **base,
            reason=(
                "Tool is not directly callable in smart mode. Use discover_tools "
                "+ get_tool_details + execute_dynamic_tool, or set ALGOCHAINS_TOOL_MODE=full."
            ),
        )

    # ARCH-RISK FIX (stdio/full parity): In full mode, ORDER_EXEC and DESTRUCTIVE
    # tools bypass the execute_dynamic_tool envelope that enforces owner_token + confirm.
    # This was an architectural backdoor: setting ALGOCHAINS_TOOL_MODE=full made
    # ALGOCHAINS_REQUIRE_CONFIRMATION=0 bypass ALL trading safety gates.
    # Fix: apply the same owner_token + confirmation gate as evaluate_dynamic_tool
    # for tier >= ORDER_EXEC, regardless of tool_mode.
    if tier >= TIER_ORDER_EXEC:
        _env_owner_token = os.environ.get("OWNER_API_TOKEN", "")
        if not _env_owner_token:
            return ToolPolicyDecision(
                False,
                **base,
                required_secret="OWNER_API_TOKEN",
                reason=(
                    f"[{label}] tool '{tool_name}' requires OWNER_API_TOKEN to be configured "
                    "before direct stdio execution can be authorized."
                ),
            )
        if owner_token != _env_owner_token:
            return ToolPolicyDecision(
                False,
                **base,
                required_secret="OWNER_API_TOKEN",
                reason=(
                    f"[{label}] tool '{tool_name}' requires owner_token authorization "
                    "even in full mode (stdio/full-mode parity enforcement). "
                    "Pass owner_token in arguments or use execute_dynamic_tool."
                ),
            )
        if require_confirmation:
            return ToolPolicyDecision(
                False,
                **base,
                reason=(
                    f"[{label}] tool '{tool_name}' requires interactive confirmation "
                    "even in full mode. Set ALGOCHAINS_REQUIRE_CONFIRMATION=0 "
                    "or use execute_dynamic_tool with confirm=true."
                ),
            )

    return ToolPolicyDecision(True, **base)
