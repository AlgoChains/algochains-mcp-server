"""
Data provider registry — manages all configured data providers.

Users configure which providers they want via environment variables.
The registry discovers and initializes available providers.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .base import DataProvider, ProviderInfo

logger = logging.getLogger("algochains_mcp.data_providers.registry")

# All known providers and their env vars
_PROVIDER_MAP: dict[str, tuple[str, str]] = {
    "polygon": ("POLYGON_API_KEY", "algochains_mcp.data_providers.polygon_provider.PolygonProvider"),
    "yahoo": ("", "algochains_mcp.data_providers.yahoo_provider.YahooFinanceProvider"),
    "alphavantage": ("ALPHA_VANTAGE_API_KEY", "algochains_mcp.data_providers.alpha_vantage_provider.AlphaVantageProvider"),
    "finnhub": ("FINNHUB_API_KEY", "algochains_mcp.data_providers.finnhub_provider.FinnhubProvider"),
    "twelvedata": ("TWELVE_DATA_API_KEY", "algochains_mcp.data_providers.twelve_data_provider.TwelveDataProvider"),
}


def _import_provider(dotted_path: str) -> type:
    """Dynamically import a provider class."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class DataProviderRegistry:
    """Discovers and manages data providers based on environment config."""

    def __init__(self):
        self._providers: dict[str, DataProvider] = {}
        self._discover()

    def _discover(self) -> None:
        """Auto-discover providers based on available env vars."""
        for name, (env_var, class_path) in _PROVIDER_MAP.items():
            # Yahoo doesn't need an API key
            if not env_var or os.environ.get(env_var):
                try:
                    cls = _import_provider(class_path)
                    api_key = os.environ.get(env_var, "") if env_var else ""
                    self._providers[name] = cls(api_key) if env_var else cls()
                    logger.info("Loaded data provider: %s", name)
                except Exception as e:
                    logger.debug("Skipping provider %s: %s", name, e)

    def get(self, name: str) -> Optional[DataProvider]:
        return self._providers.get(name)

    def list_available(self) -> list[str]:
        return list(self._providers.keys())

    def list_all_providers(self) -> list[ProviderInfo]:
        """List info for ALL known providers (even unconfigured ones)."""
        result = []
        for name, (env_var, class_path) in _PROVIDER_MAP.items():
            try:
                cls = _import_provider(class_path)
                instance = cls.__new__(cls)
                result.append(instance.info())
            except Exception:
                pass
        return result

    def get_default(self) -> Optional[DataProvider]:
        """Get the first available provider as default."""
        # Prefer paid providers over free for better data quality
        for preferred in ["polygon", "twelvedata", "finnhub", "alphavantage", "yahoo"]:
            if preferred in self._providers:
                return self._providers[preferred]
        return None

    async def health_check_all(self) -> dict[str, bool]:
        """Run health checks on all configured providers."""
        results = {}
        for name, provider in self._providers.items():
            try:
                results[name] = await provider.health_check()
            except Exception:
                results[name] = False
        return results
