"""
V17 Dynamic Toolset Gateway — BM25 search over all registered MCP tools.

The gateway maintains a BM25 index over tool metadata (name, description,
parameter names, category tags). When a user message arrives, the gateway
searches for the most relevant tools and returns only those to the LLM,
achieving 90%+ context window reduction.

Three meta-tools:
1. discover_tools — BM25 search, returns top-K tool summaries
2. get_tool_details — full inputSchema + examples for one tool
3. execute_dynamic_tool — proxy execution of any registered tool
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("algochains_mcp.dynamic_toolsets")


# ═══════════════════════════════════════════════════════════════════
# Tool metadata registry
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ToolMetadata:
    """Metadata for a single registered tool."""
    name: str
    description: str
    category: str
    version: str
    input_schema: dict = field(default_factory=dict)
    examples: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    requires_broker: bool = False
    broker_specific: Optional[str] = None
    tokens: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# BM25 index for tool discovery
# ═══════════════════════════════════════════════════════════════════

class ToolBM25Index:
    """BM25 index over tool metadata for semantic discovery."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._tools: list[ToolMetadata] = []
        self._avg_dl: float = 0.0
        self._idf: dict[str, float] = {}

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def build(self, tools: list[ToolMetadata]) -> None:
        """Build the BM25 index from tool metadata."""
        self._tools = tools
        n = len(tools)
        if n == 0:
            return

        df: dict[str, int] = {}
        total_len = 0

        for tool in tools:
            corpus = (
                f"{tool.name} {tool.description} {tool.category} "
                f"{' '.join(tool.tags)} "
                f"{' '.join(tool.input_schema.get('properties', {}).keys())}"
            )
            tool.tokens = self._tokenize(corpus)
            total_len += len(tool.tokens)
            seen: set[str] = set()
            for tok in tool.tokens:
                if tok not in seen:
                    df[tok] = df.get(tok, 0) + 1
                    seen.add(tok)

        self._avg_dl = total_len / n
        for term, doc_freq in df.items():
            self._idf[term] = math.log((n - doc_freq + 0.5) / (doc_freq + 0.5) + 1)

    def search(self, query: str, top_k: int = 10, category: Optional[str] = None) -> list[dict]:
        """Search for tools matching a natural language query."""
        if not self._tools:
            return []

        q_tokens = self._tokenize(query)
        scored: list[tuple[float, int]] = []

        for idx, tool in enumerate(self._tools):
            if category and tool.category != category:
                continue

            dl = len(tool.tokens)
            tf_map: dict[str, int] = {}
            for tok in tool.tokens:
                tf_map[tok] = tf_map.get(tok, 0) + 1

            score = 0.0
            for qt in q_tokens:
                if qt in self._idf:
                    tf = tf_map.get(qt, 0)
                    idf = self._idf[qt]
                    numerator = tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (
                        1 - self.b + self.b * dl / self._avg_dl
                    )
                    score += idf * numerator / denominator

            if score > 0:
                scored.append((score, idx))

        scored.sort(key=lambda x: -x[0])
        results = []
        for score, idx in scored[:top_k]:
            tool = self._tools[idx]
            results.append({
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "version": tool.version,
                "tags": tool.tags,
                "score": round(score, 4),
                "requires_broker": tool.requires_broker,
            })
        return results

    def get_tool(self, name: str) -> Optional[ToolMetadata]:
        """Get full metadata for a specific tool."""
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None

    def list_categories(self) -> dict[str, int]:
        """List all tool categories with counts."""
        cats: dict[str, int] = {}
        for tool in self._tools:
            cats[tool.category] = cats.get(tool.category, 0) + 1
        return cats


# ═══════════════════════════════════════════════════════════════════
# Dynamic Toolset Gateway
# ═══════════════════════════════════════════════════════════════════

ToolDispatcher = Callable[..., Coroutine[Any, Any, Any]]


class DynamicToolsetGateway:
    """
    Central gateway that manages dynamic tool discovery and execution.

    Instead of exposing all 242 tools, the gateway uses BM25 search
    to return only the most relevant tools per user message.

    Usage:
        gateway = DynamicToolsetGateway()
        gateway.register_tool(ToolMetadata(...), handler_func)
        results = gateway.discover("buy AAPL stock")
        # Returns top-10 trading-related tools
    """

    def __init__(self):
        self._index = ToolBM25Index()
        self._tools: dict[str, ToolMetadata] = {}
        self._dispatchers: dict[str, ToolDispatcher] = {}
        self._built = False

    def register_tool(
        self,
        metadata: ToolMetadata,
        dispatcher: Optional[ToolDispatcher] = None,
    ) -> None:
        """Register a tool with its metadata and optional dispatcher."""
        self._tools[metadata.name] = metadata
        if dispatcher:
            self._dispatchers[metadata.name] = dispatcher
        self._built = False

    def register_tools_from_list(
        self,
        tools: list[dict],
        category: str = "core",
        version: str = "v1",
    ) -> None:
        """Bulk register tools from MCP Tool definition list."""
        for tool_def in tools:
            name = tool_def.get("name", "")
            desc = tool_def.get("description", "")
            schema = tool_def.get("inputSchema", {})
            props = schema.get("properties", {})

            tags = [category]
            if "broker" in props:
                tags.append("broker")
            if any(k in name for k in ("order", "trade", "position")):
                tags.append("trading")
            if any(k in name for k in ("quote", "price", "chart", "bar")):
                tags.append("market_data")
            if any(k in name for k in ("backtest", "strategy", "validate")):
                tags.append("strategy")
            if any(k in name for k in ("ml", "model", "predict", "feature")):
                tags.append("ml")

            metadata = ToolMetadata(
                name=name,
                description=desc,
                category=category,
                version=version,
                input_schema=schema,
                tags=tags,
                requires_broker="broker" in schema.get("required", []),
            )
            self._tools[name] = metadata
            self._built = False

    def build_index(self) -> None:
        """Build the BM25 index from all registered tools."""
        self._index.build(list(self._tools.values()))
        self._built = True
        logger.info("Dynamic toolset index built: %d tools", len(self._tools))

    def discover(
        self,
        query: str,
        top_k: int = 10,
        category: Optional[str] = None,
    ) -> list[dict]:
        """
        Discover relevant tools for a natural language query.

        This is the primary meta-tool. Returns top-K tools with
        name, description, category, and relevance score.
        """
        if not self._built:
            self.build_index()
        return self._index.search(query, top_k, category)

    def get_tool_details(self, tool_name: str) -> Optional[dict]:
        """
        Get full details for a specific tool including inputSchema and examples.

        This is meta-tool #2. After discover_tools returns summaries,
        the LLM can request full details for tools it wants to use.
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return None
        return {
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "version": tool.version,
            "input_schema": tool.input_schema,
            "examples": tool.examples,
            "tags": tool.tags,
            "requires_broker": tool.requires_broker,
            "broker_specific": tool.broker_specific,
        }

    async def execute(self, tool_name: str, arguments: dict) -> Any:
        """
        Execute a dynamically discovered tool.

        This is meta-tool #3. Proxies execution to the registered dispatcher.
        """
        if tool_name not in self._dispatchers:
            raise ValueError(
                f"Tool '{tool_name}' has no registered dispatcher. "
                f"Use the static tool path or register a dispatcher."
            )
        return await self._dispatchers[tool_name](**arguments)

    def list_categories(self) -> dict[str, int]:
        """List all tool categories with counts."""
        if not self._built:
            self.build_index()
        return self._index.list_categories()

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    def get_meta_tools(self) -> list[dict]:
        """Return the 3 meta-tool definitions for MCP registration."""
        return [
            {
                "name": "discover_tools",
                "description": (
                    "Search for relevant AlgoChains tools using natural language. "
                    "Returns the top-K most relevant tools with descriptions. "
                    "Use this FIRST to find which tools are available for your task."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of what you want to do",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results (default 10)",
                            "default": 10,
                        },
                        "category": {
                            "type": "string",
                            "description": "Filter by category: trading, market_data, strategy, ml, analytics, alt_data, defi, cloud",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_tool_details",
                "description": (
                    "Get full details for a specific tool including its input schema, "
                    "parameter types, and usage examples. Call this after discover_tools "
                    "to get the full specification before calling execute_dynamic_tool."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "Exact tool name from discover_tools results",
                        },
                    },
                    "required": ["tool_name"],
                },
            },
            {
                "name": "execute_dynamic_tool",
                "description": (
                    "Execute any discovered tool by name with arguments. "
                    "Use discover_tools first to find tools, then get_tool_details "
                    "for the schema, then call this to execute."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "Tool name to execute",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Arguments matching the tool's inputSchema",
                        },
                    },
                    "required": ["tool_name", "arguments"],
                },
            },
        ]
