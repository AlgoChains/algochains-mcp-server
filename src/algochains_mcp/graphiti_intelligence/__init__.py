"""Graphiti temporal knowledge-graph domain for the AlgoChains MCP server.

Advisory (agent_memory authority) temporal context-graph tools. Thin wrappers over
the control-tower shared client (intelligence_platform/graphiti_client.py) which itself
wraps getzep/graphiti. Fails closed (graphiti_unavailable) when graphiti-core or Neo4j
is absent. NEVER a trading/order/risk dependency.

See control-tower docs/GRAPHITI_INTEGRATION_MEGAPROMPT.md.
"""

from .client import (  # noqa: F401
    graphiti_health,
    graphiti_search,
    graphiti_temporal_query,
    graphiti_add_episode,
    GraphitiBridgeError,
)
