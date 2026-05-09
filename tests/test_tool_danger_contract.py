from __future__ import annotations


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
