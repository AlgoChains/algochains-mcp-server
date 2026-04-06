"""
Onyx Intelligence Layer — AlgoChains Knowledge Search
======================================================
Self-hosted Onyx RAG knowledge base integration.

Provides semantic search and natural language QA over:
  - 400+ strategy research JSONs
  - 45+ implementation blueprints
  - 126+ OpenClaw skills
  - Live bot trade logs (last 7 days)
  - Marketplace strategy listings and audit reports

Onyx runs on the AlgoChains desktop GPU tower (100.89.114.31:8085)
and is accessed via Tailscale from any agent node.
"""

from .onyx_client import (
    OnyxAnswer,
    OnyxClient,
    OnyxSearchResult,
    OnyxUnavailableError,
    get_onyx_client,
    onyx_ask,
    onyx_search,
)

__all__ = [
    "OnyxClient",
    "OnyxAnswer",
    "OnyxSearchResult",
    "OnyxUnavailableError",
    "get_onyx_client",
    "onyx_search",
    "onyx_ask",
]
