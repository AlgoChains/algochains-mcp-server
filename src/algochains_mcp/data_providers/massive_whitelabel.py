"""
Massive.com Enterprise White-Label Provider for AlgoChains.

AlgoChains holds an enterprise license and white-label reselling partnership
with Massive.com. This module implements the 4-tool composable architecture:

1. search_endpoints — BM25 search over all REST endpoints from llms.txt
2. get_endpoint_docs — parameter schema for a specific endpoint
3. call_api — execute API call, store_as for in-memory DataFrames
4. query_data — SQL over stored DataFrames + apply financial functions

Built-in server-side functions (via apply parameter):
- Greeks: bs_price, bs_delta, bs_gamma, bs_theta, bs_vega, bs_rho
- Returns: simple_return, log_return, cumulative_return, sharpe_ratio, sortino_ratio
- Technicals: sma, ema

Coverage: Stocks, Options, Indices, Currencies, Futures, SEC filings
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import pandas as pd

from ..config import MassiveConfig
from ..errors import AlgoChainsError

logger = logging.getLogger("algochains_mcp.data_providers.massive")


# ═══════════════════════════════════════════════════════════════════
# Error types
# ═══════════════════════════════════════════════════════════════════

class MassiveError(AlgoChainsError):
    """Base error for Massive API operations."""
    pass


class MassiveAPIError(MassiveError):
    """Massive REST API returned an error response."""

    def __init__(self, message: str, status_code: int = 0, path: str = "", **kwargs):
        self.status_code = status_code
        self.path = path
        super().__init__(message, **kwargs)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["status_code"] = self.status_code
        d["path"] = self.path
        return d


class MassiveTableLimitError(MassiveError):
    """Max in-memory tables exceeded."""
    pass


class MassiveQueryError(MassiveError):
    """SQL query against stored DataFrames failed."""
    pass


# ═══════════════════════════════════════════════════════════════════
# BM25 search index — built from llms.txt at startup
# ═══════════════════════════════════════════════════════════════════

@dataclass
class EndpointEntry:
    """A single endpoint parsed from llms.txt."""
    name: str
    method: str
    path: str
    description: str
    docs_url: str
    tokens: list[str] = field(default_factory=list)


class BM25Index:
    """Simple BM25 search index for endpoint discovery."""

    def __init__(self, entries: list[EndpointEntry], k1: float = 1.5, b: float = 0.75):
        self.entries = entries
        self.k1 = k1
        self.b = b
        self._avg_dl = 0.0
        self._idf: dict[str, float] = {}
        self._build()

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _build(self) -> None:
        n = len(self.entries)
        if n == 0:
            return
        df: dict[str, int] = {}
        total_len = 0
        for entry in self.entries:
            entry.tokens = self._tokenize(
                f"{entry.name} {entry.description} {entry.path}"
            )
            total_len += len(entry.tokens)
            seen: set[str] = set()
            for tok in entry.tokens:
                if tok not in seen:
                    df[tok] = df.get(tok, 0) + 1
                    seen.add(tok)
        self._avg_dl = total_len / n
        for term, doc_freq in df.items():
            self._idf[term] = math.log((n - doc_freq + 0.5) / (doc_freq + 0.5) + 1)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not self.entries:
            return []
        q_tokens = self._tokenize(query)
        scores: list[tuple[float, int]] = []
        for idx, entry in enumerate(self.entries):
            score = 0.0
            dl = len(entry.tokens)
            tf_map: dict[str, int] = {}
            for tok in entry.tokens:
                tf_map[tok] = tf_map.get(tok, 0) + 1
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
                scores.append((score, idx))
        scores.sort(key=lambda x: -x[0])
        results = []
        for score, idx in scores[:top_k]:
            e = self.entries[idx]
            results.append({
                "name": e.name,
                "method": e.method,
                "path": e.path,
                "description": e.description,
                "docs_url": e.docs_url,
                "score": round(score, 4),
            })
        return results


# ═══════════════════════════════════════════════════════════════════
# Built-in financial functions (server-side apply)
# ═══════════════════════════════════════════════════════════════════

def _apply_sma(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    col = inputs["column"]
    window = int(inputs.get("window", 20))
    df[output] = df[col].rolling(window=window, min_periods=1).mean()
    return df


def _apply_ema(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    col = inputs["column"]
    window = int(inputs.get("window", 20))
    df[output] = df[col].ewm(span=window, adjust=False).mean()
    return df


def _apply_simple_return(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    col = inputs["column"]
    df[output] = df[col].pct_change()
    return df


def _apply_log_return(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    col = inputs["column"]
    import numpy as np
    df[output] = np.log(df[col] / df[col].shift(1))
    return df


def _apply_cumulative_return(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    col = inputs["column"]
    df[output] = (1 + df[col].pct_change()).cumprod() - 1
    return df


def _apply_sharpe_ratio(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    col = inputs["column"]
    risk_free = float(inputs.get("risk_free", 0.0))
    window = int(inputs.get("window", 252))
    returns = df[col].pct_change()
    rolling_mean = returns.rolling(window=window, min_periods=2).mean()
    rolling_std = returns.rolling(window=window, min_periods=2).std()
    df[output] = (rolling_mean - risk_free / 252) / rolling_std * math.sqrt(252)
    return df


def _apply_sortino_ratio(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    col = inputs["column"]
    risk_free = float(inputs.get("risk_free", 0.0))
    window = int(inputs.get("window", 252))
    returns = df[col].pct_change()
    rolling_mean = returns.rolling(window=window, min_periods=2).mean()
    downside = returns.copy()
    downside[downside > 0] = 0
    rolling_downside_std = downside.rolling(window=window, min_periods=2).std()
    df[output] = (rolling_mean - risk_free / 252) / rolling_downside_std * math.sqrt(252)
    return df


def _bs_d1(spot: float, strike: float, vol: float, rate: float, t: float) -> float:
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    return (math.log(spot / strike) + (rate + 0.5 * vol ** 2) * t) / (vol * math.sqrt(t))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x ** 2) / math.sqrt(2 * math.pi)


def _apply_bs_price(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    spot_col = inputs["spot"]
    strike_col = inputs["strike"]
    vol_col = inputs["vol"]
    rate = float(inputs.get("rate", 0.05))
    time_col = inputs["time"]
    cp = inputs.get("type", "call")

    def _calc(row):
        s, k, v, t_days = row[spot_col], row[strike_col], row[vol_col], row[time_col]
        t = t_days / 365.0 if t_days > 0 else 0.001
        d1 = _bs_d1(s, k, v, rate, t)
        d2 = d1 - v * math.sqrt(t)
        if cp == "call":
            return s * _norm_cdf(d1) - k * math.exp(-rate * t) * _norm_cdf(d2)
        return k * math.exp(-rate * t) * _norm_cdf(-d2) - s * _norm_cdf(-d1)

    df[output] = df.apply(_calc, axis=1)
    return df


def _apply_bs_delta(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    spot_col = inputs["spot"]
    strike_col = inputs["strike"]
    vol_col = inputs["vol"]
    rate = float(inputs.get("rate", 0.05))
    time_col = inputs["time"]
    cp = inputs.get("type", "call")

    def _calc(row):
        s, k, v, t_days = row[spot_col], row[strike_col], row[vol_col], row[time_col]
        t = t_days / 365.0 if t_days > 0 else 0.001
        d1 = _bs_d1(s, k, v, rate, t)
        return _norm_cdf(d1) if cp == "call" else _norm_cdf(d1) - 1

    df[output] = df.apply(_calc, axis=1)
    return df


def _apply_bs_gamma(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    spot_col = inputs["spot"]
    strike_col = inputs["strike"]
    vol_col = inputs["vol"]
    rate = float(inputs.get("rate", 0.05))
    time_col = inputs["time"]

    def _calc(row):
        s, k, v, t_days = row[spot_col], row[strike_col], row[vol_col], row[time_col]
        t = t_days / 365.0 if t_days > 0 else 0.001
        d1 = _bs_d1(s, k, v, rate, t)
        return _norm_pdf(d1) / (s * v * math.sqrt(t)) if s > 0 and v > 0 else 0.0

    df[output] = df.apply(_calc, axis=1)
    return df


def _apply_bs_theta(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    spot_col = inputs["spot"]
    strike_col = inputs["strike"]
    vol_col = inputs["vol"]
    rate = float(inputs.get("rate", 0.05))
    time_col = inputs["time"]
    cp = inputs.get("type", "call")

    def _calc(row):
        s, k, v, t_days = row[spot_col], row[strike_col], row[vol_col], row[time_col]
        t = t_days / 365.0 if t_days > 0 else 0.001
        d1 = _bs_d1(s, k, v, rate, t)
        d2 = d1 - v * math.sqrt(t)
        first_term = -(s * _norm_pdf(d1) * v) / (2 * math.sqrt(t))
        if cp == "call":
            return (first_term - rate * k * math.exp(-rate * t) * _norm_cdf(d2)) / 365
        return (first_term + rate * k * math.exp(-rate * t) * _norm_cdf(-d2)) / 365

    df[output] = df.apply(_calc, axis=1)
    return df


def _apply_bs_vega(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    spot_col = inputs["spot"]
    strike_col = inputs["strike"]
    vol_col = inputs["vol"]
    rate = float(inputs.get("rate", 0.05))
    time_col = inputs["time"]

    def _calc(row):
        s, k, v, t_days = row[spot_col], row[strike_col], row[vol_col], row[time_col]
        t = t_days / 365.0 if t_days > 0 else 0.001
        d1 = _bs_d1(s, k, v, rate, t)
        return s * _norm_pdf(d1) * math.sqrt(t) / 100

    df[output] = df.apply(_calc, axis=1)
    return df


def _apply_bs_rho(df: pd.DataFrame, inputs: dict, output: str) -> pd.DataFrame:
    spot_col = inputs["spot"]
    strike_col = inputs["strike"]
    vol_col = inputs["vol"]
    rate = float(inputs.get("rate", 0.05))
    time_col = inputs["time"]
    cp = inputs.get("type", "call")

    def _calc(row):
        s, k, v, t_days = row[spot_col], row[strike_col], row[vol_col], row[time_col]
        t = t_days / 365.0 if t_days > 0 else 0.001
        d1 = _bs_d1(s, k, v, rate, t)
        d2 = d1 - v * math.sqrt(t)
        if cp == "call":
            return k * t * math.exp(-rate * t) * _norm_cdf(d2) / 100
        return -k * t * math.exp(-rate * t) * _norm_cdf(-d2) / 100

    df[output] = df.apply(_calc, axis=1)
    return df


APPLY_FUNCTIONS: dict[str, Any] = {
    "sma": _apply_sma,
    "ema": _apply_ema,
    "simple_return": _apply_simple_return,
    "log_return": _apply_log_return,
    "cumulative_return": _apply_cumulative_return,
    "sharpe_ratio": _apply_sharpe_ratio,
    "sortino_ratio": _apply_sortino_ratio,
    "bs_price": _apply_bs_price,
    "bs_delta": _apply_bs_delta,
    "bs_gamma": _apply_bs_gamma,
    "bs_theta": _apply_bs_theta,
    "bs_vega": _apply_bs_vega,
    "bs_rho": _apply_bs_rho,
}


# ═══════════════════════════════════════════════════════════════════
# Massive White-Label Provider
# ═══════════════════════════════════════════════════════════════════

class MassiveWhiteLabelProvider:
    """
    White-label Massive data provider for AlgoChains marketplace.

    Implements the 4-tool composable architecture:
    - search_endpoints: BM25 search over all API endpoints
    - get_endpoint_docs: parameter docs for a specific endpoint
    - call_api: execute GET, optionally store as DataFrame
    - query_data: SQL over stored DataFrames + apply functions
    """

    def __init__(self, config: MassiveConfig):
        self.cfg = config
        self._bm25_index: Optional[BM25Index] = None
        self._dataframes: dict[str, pd.DataFrame] = {}
        self._table_timestamps: dict[str, float] = {}
        self._raw_llms_txt: str = ""
        self._endpoint_docs_cache: dict[str, dict] = {}
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=60)

    async def startup(self) -> None:
        """Build BM25 search index from Massive's llms.txt endpoint catalog."""
        if not self.cfg.api_key:
            logger.warning("Massive API key not configured — skipping startup")
            return
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(self.cfg.llms_txt_url)
                resp.raise_for_status()
                self._raw_llms_txt = resp.text
                entries = self._parse_llms_txt(resp.text)
                self._bm25_index = BM25Index(entries)
                logger.info(
                    "Massive BM25 index built: %d endpoints indexed",
                    len(entries),
                )
        except Exception as e:
            logger.error("Failed to build Massive BM25 index: %s", e)
            self._bm25_index = BM25Index([])

    def _parse_llms_txt(self, text: str) -> list[EndpointEntry]:
        """Parse llms.txt format into endpoint entries.

        Handles Massive's markdown format:
            - [Name](docs_url): Description
        as well as the structured key:value format.
        """
        entries: list[EndpointEntry] = []

        # Pattern for Massive's markdown link format:
        #   - [Name](https://massive.com/docs/rest/.../page.md): Description
        md_link_re = re.compile(
            r'^[-*]\s+\[([^\]]+)\]\(([^)]+)\)(?::\s*(.*))?'
        )

        current_name = ""
        current_method = "GET"
        current_path = ""
        current_desc = ""
        current_docs = ""

        for line in text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(">"):
                if current_name and (current_path or current_docs):
                    entries.append(EndpointEntry(
                        name=current_name,
                        method=current_method,
                        path=current_path or current_docs,
                        description=current_desc,
                        docs_url=current_docs,
                    ))
                    current_name = ""
                    current_method = "GET"
                    current_path = ""
                    current_desc = ""
                    current_docs = ""
                continue

            # Try markdown link format first
            md_match = md_link_re.match(line)
            if md_match:
                # Flush any pending entry
                if current_name and (current_path or current_docs):
                    entries.append(EndpointEntry(
                        name=current_name,
                        method=current_method,
                        path=current_path or current_docs,
                        description=current_desc,
                        docs_url=current_docs,
                    ))

                current_name = md_match.group(1).strip()
                current_docs = md_match.group(2).strip()
                current_desc = (md_match.group(3) or "").strip()
                current_method = "GET"
                current_path = current_docs
                continue

            # Fallback: structured key:value format
            if line.startswith("- ") or line.startswith("* "):
                parts = line[2:].strip().split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip().lower()
                    val = parts[1].strip()
                    if key in ("name", "title"):
                        current_name = val
                    elif key in ("method",):
                        current_method = val.upper()
                    elif key in ("path", "url", "endpoint"):
                        current_path = val
                    elif key in ("description", "desc"):
                        current_desc = val
                    elif key in ("docs", "docs_url", "documentation"):
                        current_docs = val
            elif current_name and not current_desc:
                current_desc = line

        if current_name and (current_path or current_docs):
            entries.append(EndpointEntry(
                name=current_name,
                method=current_method,
                path=current_path or current_docs,
                description=current_desc,
                docs_url=current_docs,
            ))
        return entries

    def _expire_tables(self) -> None:
        """Remove tables older than 1 hour."""
        now = time.monotonic()
        expired = [
            name for name, ts in self._table_timestamps.items()
            if now - ts > 3600
        ]
        for name in expired:
            self._dataframes.pop(name, None)
            self._table_timestamps.pop(name, None)
            logger.debug("Expired table: %s", name)

    def search_endpoints(self, query: str, top_k: int = 5, scope: str = "all") -> list[dict]:
        """BM25 search over all Massive API endpoints."""
        if self._bm25_index is None:
            raise MassiveError("Massive BM25 index not initialized — call startup() first")
        results = self._bm25_index.search(query, top_k)
        if scope == "functions":
            return [
                {"name": name, "description": f"Server-side function: {name}"}
                for name in APPLY_FUNCTIONS
                if query.lower() in name.lower() or not query
            ]
        return results

    async def get_endpoint_docs(self, docs_url: str) -> dict:
        """Fetch parameter documentation for a specific endpoint."""
        if docs_url in self._endpoint_docs_cache:
            return self._endpoint_docs_cache[docs_url]

        try:
            headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
            resp = await self._http.get(docs_url, headers=headers)
            resp.raise_for_status()
            docs = resp.json()
            self._endpoint_docs_cache[docs_url] = docs
            return docs
        except httpx.HTTPStatusError as e:
            raise MassiveAPIError(
                f"Failed to fetch docs: {e.response.status_code}",
                status_code=e.response.status_code,
                path=docs_url,
            )
        except Exception as e:
            raise MassiveError(f"Failed to fetch endpoint docs: {e}")

    async def call_api(
        self,
        path: str,
        method: str = "GET",
        params: Optional[dict] = None,
        store_as: Optional[str] = None,
        apply: Optional[list[dict]] = None,
        api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_provider: Optional[str] = None,
    ) -> dict:
        """
        Execute Massive API call, optionally store result as DataFrame.

        Args:
            path: API endpoint path (e.g. /v2/aggs/ticker/AAPL/range/1/day/...)
            method: HTTP method (only GET supported)
            params: Query parameters
            store_as: Table name to store results as DataFrame
            apply: List of function steps for post-processing
            api_key: Override API key for this request (white-label customer isolation)
            llm_model: LLM model name for usage analytics tracking
            llm_provider: LLM provider name for usage analytics tracking
        """
        self._expire_tables()

        if store_as:
            store_as = re.sub(r"[^a-zA-Z0-9_]", "_", store_as)[:63]
        if store_as and len(self._dataframes) >= self.cfg.max_tables:
            raise MassiveTableLimitError(
                f"Max tables ({self.cfg.max_tables}) reached. "
                f"Use query_data(sql='DROP TABLE <name>') to free space."
            )

        key = api_key or self.cfg.api_key
        url = f"{self.cfg.base_url}{path}"
        headers = {"Authorization": f"Bearer {key}"}
        if llm_model:
            headers["X-LLM-Model"] = llm_model
        if llm_provider:
            headers["X-LLM-Provider"] = llm_provider

        try:
            resp = await self._http.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            raise MassiveAPIError(
                f"Massive API error: {e.response.status_code} for {path}",
                status_code=e.response.status_code,
                path=path,
            )
        except Exception as e:
            raise MassiveError(f"Massive API call failed: {e}")

        # Pagination auto-detection — append next-page hint like real Massive server
        next_url = data.get("next_url") or data.get("next")
        if next_url:
            next_path = next_url.replace(self.cfg.base_url, "") if next_url.startswith("http") else next_url
            next_params = {}
            if "?" in next_path:
                next_path, qs = next_path.split("?", 1)
                for pair in qs.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        next_params[k] = v
            data["_next_page"] = {
                "hint": "Next page available. Call massive_call_api with the path and params below.",
                "path": next_path,
                "params": next_params,
            }

        if store_as:
            results_key = "results"
            for rk in ("results", "data", "ticks", "bars", "trades", "quotes"):
                if rk in data:
                    results_key = rk
                    break
            rows = data.get(results_key, [])
            if isinstance(rows, list) and rows:
                df = pd.DataFrame(rows)
                if len(df) > self.cfg.max_rows:
                    df = df.head(self.cfg.max_rows)
                    data["_truncated"] = True
                    data["_max_rows"] = self.cfg.max_rows
                self._dataframes[store_as] = df
                self._table_timestamps[store_as] = time.monotonic()
                data["_stored_as"] = store_as
                data["_rows"] = len(df)
                data["_columns"] = list(df.columns)
                logger.info("Stored %d rows as '%s'", len(df), store_as)

        if apply:
            data = self._apply_functions(data, apply, store_as)

        return data

    def _apply_functions(
        self, data: dict, steps: list[dict], table_name: Optional[str] = None
    ) -> dict:
        """Apply server-side functions to data or stored DataFrame."""
        target_df = self._dataframes.get(table_name) if table_name else None

        if target_df is None:
            results_key = "results"
            for key in ("results", "data", "ticks", "bars"):
                if key in data and isinstance(data[key], list):
                    results_key = key
                    break
            rows = data.get(results_key, [])
            if rows:
                target_df = pd.DataFrame(rows)
            else:
                return data

        for step in steps:
            func_name = step.get("function", "")
            inputs = step.get("inputs", {})
            output = step.get("output", func_name)

            if func_name not in APPLY_FUNCTIONS:
                logger.warning("Unknown apply function: %s", func_name)
                continue

            try:
                target_df = APPLY_FUNCTIONS[func_name](target_df, inputs, output)
            except Exception as e:
                logger.error("Apply function '%s' failed: %s", func_name, e)

        if table_name and table_name in self._dataframes:
            self._dataframes[table_name] = target_df

        data["_applied"] = [s.get("function") for s in steps]
        return data

    async def query_data(
        self,
        sql: str,
        apply: Optional[list[dict]] = None,
    ) -> dict:
        """
        SQL queries over stored DataFrames.

        Special commands:
        - SHOW TABLES: list stored tables
        - DESCRIBE <table>: show table schema
        - DROP TABLE <table>: remove a table
        """
        sql_stripped = sql.strip()
        upper = sql_stripped.upper()

        if upper == "SHOW TABLES":
            tables = []
            for name, df in self._dataframes.items():
                tables.append({
                    "name": name,
                    "rows": len(df),
                    "columns": list(df.columns),
                    "age_seconds": round(
                        time.monotonic() - self._table_timestamps.get(name, 0), 1
                    ),
                })
            return {"tables": tables, "count": len(tables)}

        if upper.startswith("DESCRIBE "):
            table_name = sql_stripped[9:].strip().strip(";")
            df = self._dataframes.get(table_name)
            if df is None:
                raise MassiveQueryError(f"Table '{table_name}' not found")
            return {
                "table": table_name,
                "rows": len(df),
                "columns": [
                    {"name": col, "dtype": str(df[col].dtype)}
                    for col in df.columns
                ],
            }

        if upper.startswith("DROP TABLE "):
            table_name = sql_stripped[11:].strip().strip(";")
            if table_name in self._dataframes:
                del self._dataframes[table_name]
                self._table_timestamps.pop(table_name, None)
                return {"dropped": table_name}
            raise MassiveQueryError(f"Table '{table_name}' not found")

        import sqlite3
        conn = sqlite3.connect(":memory:")
        try:
            sql_lower = sql_stripped.lower()
            referenced = [
                name for name in self._dataframes
                if name.lower() in sql_lower
            ]
            if not referenced:
                referenced = list(self._dataframes.keys())
            for name in referenced:
                self._dataframes[name].to_sql(name, conn, index=False, if_exists="replace")

            result_df = pd.read_sql_query(sql_stripped, conn)
        except Exception as e:
            raise MassiveQueryError(f"SQL query failed: {e}")
        finally:
            conn.close()

        if apply:
            for step in apply:
                func_name = step.get("function", "")
                inputs = step.get("inputs", {})
                output = step.get("output", func_name)
                if func_name in APPLY_FUNCTIONS:
                    try:
                        result_df = APPLY_FUNCTIONS[func_name](result_df, inputs, output)
                    except Exception as e:
                        logger.error("Apply function '%s' failed: %s", func_name, e)

        records = result_df.to_dict(orient="records")
        return {
            "results": records,
            "count": len(records),
            "columns": list(result_df.columns),
        }

    async def scoped_key_for_customer(
        self, customer_id: str, tier: str
    ) -> dict:
        """Generate scoped API key for white-label customer."""
        tier_limits = {
            "starter": {"calls_per_month": 10_000, "assets": ["us_equities"], "realtime": False},
            "pro": {"calls_per_month": 100_000, "assets": ["us_equities", "options", "forex"], "realtime": True},
            "enterprise": {"calls_per_month": -1, "assets": ["all"], "realtime": True},
        }
        limits = tier_limits.get(tier, tier_limits["starter"])
        return {
            "customer_id": customer_id,
            "tier": tier,
            "limits": limits,
            "status": "provisioned",
        }

    async def run_pipeline(
        self,
        search_query: str,
        path_override: Optional[str] = None,
        params: Optional[dict] = None,
        store_as: Optional[str] = None,
        sql: Optional[str] = None,
        apply: Optional[list[dict]] = None,
    ) -> dict:
        """
        Composable pipeline: search → fetch → store → query → apply in 1 call.

        Reduces 4 round-trips to 1. Steps:
        1. Search endpoints for the best match (unless path_override given)
        2. Call the API and store results as a DataFrame
        3. Optionally run SQL over the stored data
        4. Optionally apply server-side functions (Greeks, technicals, returns)

        Args:
            search_query: Natural language query to find the right endpoint
            path_override: Skip search and use this path directly
            params: Query parameters for the API call
            store_as: Table name (auto-generated from search_query if omitted)
            sql: SQL to run after storing (e.g. "SELECT * FROM {table} WHERE close > 190")
            apply: Post-processing functions to apply to final results
        """
        pipeline_log: list[dict] = []

        # Step 1: Search for endpoint (or use override)
        if path_override:
            api_path = path_override
            pipeline_log.append({"step": "search", "skipped": True, "path": api_path})
        else:
            results = self.search_endpoints(search_query, top_k=1)
            if not results:
                raise MassiveError(f"No endpoints found for query: {search_query}")
            api_path = results[0]["path"]
            pipeline_log.append({"step": "search", "query": search_query, "matched": results[0]["name"], "path": api_path})

        # Step 2: Auto-generate table name if not provided; always sanitize
        if not store_as:
            store_as = re.sub(r"[^a-z0-9_]", "_", search_query.lower())[:32]
        else:
            store_as = re.sub(r"[^a-zA-Z0-9_]", "_", store_as)[:63]

        # Step 3: Call API and store
        api_result = await self.call_api(
            path=api_path, params=params, store_as=store_as,
        )
        pipeline_log.append({
            "step": "call_api",
            "path": api_path,
            "stored_as": store_as,
            "rows": api_result.get("_rows", 0),
        })

        # Step 4: Optional SQL query
        query_result = None
        if sql:
            resolved_sql = sql.replace("{table}", store_as)
            query_result = await self.query_data(sql=resolved_sql, apply=apply)
            pipeline_log.append({
                "step": "query",
                "sql": resolved_sql,
                "result_count": query_result.get("count", 0),
            })
        elif apply:
            query_result = await self.query_data(
                sql=f"SELECT * FROM {store_as}", apply=apply,
            )
            pipeline_log.append({
                "step": "query+apply",
                "functions": [s.get("function") for s in apply],
                "result_count": query_result.get("count", 0),
            })

        return {
            "pipeline": pipeline_log,
            "api_result_summary": {
                "rows": api_result.get("_rows", 0),
                "columns": api_result.get("_columns", []),
                "stored_as": store_as,
            },
            "query_result": query_result,
            "status": "completed",
        }

    def list_tables(self) -> list[str]:
        """List currently stored DataFrames."""
        return list(self._dataframes.keys())

    def get_table(self, name: str) -> Optional[pd.DataFrame]:
        """Get a stored DataFrame by name."""
        return self._dataframes.get(name)
