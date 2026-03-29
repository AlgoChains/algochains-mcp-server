"""
Optional data provider connectors for AlgoChains MCP Server.

Each provider is optional — install only the ones you need.
All providers implement the same DataProvider interface for
consistent usage across the MCP server tools.

Supported providers:
  - Polygon.io    (real-time + historical, stocks/options/crypto/forex)
  - Databento     (institutional-grade tick data, futures/equities)
  - Alpha Vantage (free tier, fundamentals + technicals)
  - Finnhub       (real-time quotes, news, fundamentals, free tier)
  - Yahoo Finance (free, no API key, stocks/ETFs/crypto)
  - Twelve Data   (real-time + historical, WebSocket, 800+ exchanges)
  - Tiingo        (EOD + IEX real-time, news, crypto)
  - Coinbase      (crypto spot + advanced trade API)
  - Binance       (crypto spot + futures, WebSocket)
  - FRED          (Federal Reserve economic data, free)
  - Unusual Whales (options flow, dark pool, Congress trades)
  - Intrinio      (institutional fundamentals + real-time)
"""
