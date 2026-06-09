"""
AlgoChains MCP Server — Universal broker connectors and marketplace integration.

Exposes trading, market data, portfolio management, and strategy validation
tools via the Model Context Protocol (MCP) for any AI agent.
"""
from importlib.metadata import version as _pkg_version, PackageNotFoundError as _PkgNotFound

try:
    __version__ = _pkg_version("algochains-mcp-server")
except _PkgNotFound:
    # Running directly from source without pip install -e .
    __version__ = "dev"
