from __future__ import annotations

import ast
import collections
import os


SIDE_EFFECT_HINTS = (
    "activate",
    "approve",
    "cancel",
    "close",
    "delete",
    "deploy",
    "execute",
    "flatten",
    "modify",
    "payment",
    "place",
    "process",
    "restart",
    "run_",
    "set_",
    "start_",
    "submit",
    "upload",
)


def test_side_effect_named_tools_have_deliberate_tier_source():
    import algochains_mcp.server as srv
    from algochains_mcp.tool_danger_tiers import get_danger_tier_source

    missing = []
    for tool in srv.TOOLS_ANNOTATED:
        name = tool.name
        if name.startswith(SIDE_EFFECT_HINTS) or "_order" in name or "order_" in name:
            source = get_danger_tier_source(name)
            if source == "default":
                missing.append(name)

    assert not missing, (
        "Side-effect-looking MCP tools must be explicitly tiered or covered by "
        f"a deliberate prefix rule: {sorted(missing)}"
    )


def test_trade_annotations_agree_with_danger_tiers():
    import algochains_mcp.server as srv
    from algochains_mcp.tool_danger_tiers import TIER_ORDER_EXEC, get_danger_tier

    mismatches = []
    for tool in srv.TOOLS_ANNOTATED:
        ann = getattr(tool, "annotations", None)
        tier = get_danger_tier(tool.name)
        destructive = bool(getattr(ann, "destructiveHint", False)) if ann is not None else False
        read_only = bool(getattr(ann, "readOnlyHint", False)) if ann is not None else False
        if tier >= TIER_ORDER_EXEC and not destructive:
            mismatches.append((tool.name, "tier>=ORDER_EXEC but annotation is not destructive"))
        if tier >= TIER_ORDER_EXEC and read_only:
            mismatches.append((tool.name, "tier>=ORDER_EXEC but annotation is read-only"))

    assert not mismatches


# ── duplicate-tier-key guard (makes the dead "P2-7" comment an enforced contract) ──
#
# tool_danger_tiers.py documents that _TOOL_TIERS may contain intentional duplicate
# keys (validate_strategy) and that "last wins". The module ships an
# _EXPECTED_INTENTIONAL_DUPES set + a comment claiming an "assertion guards against
# UNINTENTIONAL future duplicates" — but no assertion actually runs, and the comment's
# premise ("built from multiple merged dicts") is wrong: it is a single ANNOTATED dict
# literal, so Python collapses duplicate keys at COMPILE time and the dup is
# unrecoverable from the runtime dict. The real danger is a *future* duplicate that
# silently DOWNGRADES a tool's tier (e.g. a DESTRUCTIVE tool re-listed as READ_ONLY).
# These tests parse the source AST (the only place the dup survives) and enforce it.

def _tier_module_path() -> str:
    import algochains_mcp.tool_danger_tiers as tdt
    return tdt.__file__


def _tool_tiers_dup_sequences() -> dict[str, list[str]]:
    """Return {tool_name: [tier_name_in_source_order, ...]} for keys that appear
    more than once in the _TOOL_TIERS dict literal. Handles the AnnAssign form."""
    src = open(_tier_module_path(), encoding="utf-8").read()
    tree = ast.parse(src)
    order: dict[str, list[str]] = collections.defaultdict(list)
    for node in ast.walk(tree):
        target_name = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    target_name, value = t.id, node.value
        if target_name == "_TOOL_TIERS" and isinstance(value, ast.Dict):
            for k, v in zip(value.keys, value.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    if isinstance(v, ast.Name):
                        order[k.value].append(v.id)
                    elif isinstance(v, ast.Constant):
                        order[k.value].append(repr(v.value))
                    else:
                        order[k.value].append(ast.dump(v))
    return {name: seq for name, seq in order.items() if len(seq) > 1}


def test_no_unexpected_duplicate_tier_keys():
    """Every duplicate key in _TOOL_TIERS must be explicitly whitelisted.

    This is the assertion the 'P2-7 FIX' comment promised but never wired up.
    """
    from algochains_mcp.tool_danger_tiers import _EXPECTED_INTENTIONAL_DUPES

    dups = _tool_tiers_dup_sequences()
    unexpected = sorted(set(dups) - set(_EXPECTED_INTENTIONAL_DUPES))
    assert not unexpected, (
        "Unexpected duplicate keys in _TOOL_TIERS (Python silently keeps the LAST "
        f"value, which can hide a tier change): {unexpected}. If intentional, add to "
        "_EXPECTED_INTENTIONAL_DUPES in tool_danger_tiers.py with a justifying comment."
    )


def test_duplicate_tier_keys_never_silently_downgrade():
    """A duplicate key must not LOWER a tool's effective danger tier.

    The effective tier is the last occurrence ('last wins'). If an earlier occurrence
    was MORE dangerous, the duplicate silently weakened a safety gate — refuse it even
    for whitelisted dupes (validate_strategy is fine: READ_ONLY -> WRITE_LOCAL is a
    no-op-or-raise change, never a downgrade)."""
    import algochains_mcp.tool_danger_tiers as tdt

    dups = _tool_tiers_dup_sequences()
    downgrades = []
    for name, tier_names in dups.items():
        tiers = [getattr(tdt, tn, None) for tn in tier_names]
        tiers = [t for t in tiers if isinstance(t, int)]
        if len(tiers) < 2:
            continue
        effective = tiers[-1]
        if effective < max(tiers):
            downgrades.append((name, tier_names, f"effective={effective} < max={max(tiers)}"))
    assert not downgrades, (
        "Duplicate _TOOL_TIERS keys that SILENTLY DOWNGRADE the danger tier "
        f"(last-wins is less dangerous than an earlier entry): {downgrades}"
    )
