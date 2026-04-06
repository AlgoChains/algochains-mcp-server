"""
V17 Dynamic Toolsets — BM25 semantic search over tools.

Instead of exposing all 242 tools to the LLM context window, this module
uses BM25 search to dynamically select the 5-10 most relevant tools per
user message. Achieves 90%+ context reduction.

Meta-tools:
- discover_tools: BM25 search over all registered tools
- get_tool_details: full schema + examples for a specific tool
- execute_dynamic_tool: proxy execution of any discovered tool
"""
from .gateway import DynamicToolsetGateway

__all__ = ["DynamicToolsetGateway"]
