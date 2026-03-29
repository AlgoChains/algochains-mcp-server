"""
Proprietary Dataset Builder — Ingest from all validated data providers,
normalize schemas, enrich with features, and export ML-ready datasets.

Turns scattered API keys into a unified, versioned feature store that
powers ML models for marketplace bots.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("algochains.datasets")

DEFAULT_DATASET_DIR = Path.home() / ".algochains" / "datasets"


class DatasetFormat(str, Enum):
    PARQUET = "parquet"
    CSV = "csv"
    JSON = "json"


class Timeframe(str, Enum):
    MINUTE_1 = "1min"
    MINUTE_5 = "5min"
    MINUTE_15 = "15min"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAILY = "daily"
    WEEKLY = "weekly"


class EnrichmentType(str, Enum):
    TECHNICAL_INDICATORS = "technical_indicators"
    SENTIMENT = "sentiment"
    CROSS_ASSET_CORRELATION = "cross_asset_correlation"
    REGIME_LABELS = "regime_labels"
    VOLUME_PROFILE = "volume_profile"
    CALENDAR_FEATURES = "calendar_features"


@dataclass
class DatasetMeta:
    dataset_id: str
    symbol: str
    timeframe: str
    sources: list[str]
    rows: int
    columns: list[str]
    date_range_start: str
    date_range_end: str
    enrichments: list[str]
    created_at: str
    updated_at: str
    size_bytes: int
    format: str
    path: str
    version: int = 1
    checksum: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "sources": self.sources,
            "rows": self.rows,
            "columns": self.columns,
            "date_range": f"{self.date_range_start} → {self.date_range_end}",
            "enrichments": self.enrichments,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "size_bytes": self.size_bytes,
            "size_human": self._human_size(self.size_bytes),
            "format": self.format,
            "path": self.path,
            "version": self.version,
            "checksum": self.checksum,
        }

    @staticmethod
    def _human_size(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size //= 1024
        return f"{size:.1f} TB"


@dataclass
class DatasetRequest:
    symbol: str
    timeframe: str = "daily"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    providers: Optional[list[str]] = None  # None = use all available
    enrichments: list[str] = field(default_factory=list)
    format: str = "parquet"


class DatasetBuilder:
    """Build proprietary datasets from all available data providers."""

    def __init__(self, dataset_dir: Path | None = None) -> None:
        self._dataset_dir = dataset_dir or DEFAULT_DATASET_DIR
        self._dataset_dir.mkdir(parents=True, exist_ok=True)
        self._meta_file = self._dataset_dir / "datasets.json"
        self._datasets: dict[str, DatasetMeta] = {}
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load dataset metadata from disk."""
        if self._meta_file.exists():
            try:
                data = json.loads(self._meta_file.read_text())
                for ds_id, meta in data.items():
                    self._datasets[ds_id] = DatasetMeta(**meta)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"Failed to load dataset metadata: {e}")

    def _save_metadata(self) -> None:
        """Persist dataset metadata to disk."""
        data = {}
        for ds_id, meta in self._datasets.items():
            d = meta.to_dict()
            # Store raw fields for reconstruction
            d["date_range_start"] = meta.date_range_start
            d["date_range_end"] = meta.date_range_end
            del d["date_range"]
            del d["size_human"]
            data[ds_id] = d
        self._meta_file.write_text(json.dumps(data, indent=2))

    @staticmethod
    def _generate_id(symbol: str, timeframe: str) -> str:
        raw = f"{symbol}_{timeframe}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    # ── Build Dataset ────────────────────────────────────────────

    async def build_dataset(self, request: DatasetRequest) -> dict[str, Any]:
        """
        Build a proprietary dataset for a symbol/timeframe using
        all available data providers.
        """
        symbol = request.symbol.upper()
        tf = request.timeframe
        ds_id = self._generate_id(symbol, tf)

        # Determine which providers to use
        available_providers = self._get_available_providers()
        target_providers = request.providers or available_providers

        if not target_providers:
            return {
                "error": "No data providers configured. Run 'discover_keys' first.",
                "success": False,
            }

        # Collect data from each provider
        all_data: list[dict[str, Any]] = []
        sources_used: list[str] = []
        errors: list[str] = []

        for provider_name in target_providers:
            if provider_name not in available_providers:
                errors.append(f"{provider_name}: no API key configured")
                continue
            try:
                data = await self._fetch_from_provider(
                    provider_name, symbol, tf,
                    request.start_date, request.end_date,
                )
                if data:
                    all_data.extend(data)
                    sources_used.append(provider_name)
            except Exception as e:
                errors.append(f"{provider_name}: {str(e)}")
                logger.warning(f"Failed to fetch from {provider_name}: {e}")

        if not all_data:
            return {
                "error": "No data collected from any provider",
                "errors": errors,
                "success": False,
            }

        # Normalize and deduplicate
        normalized = self._normalize_data(all_data)
        deduped = self._deduplicate(normalized)

        # Apply enrichments
        enrichments_applied = []
        if request.enrichments:
            for enrichment in request.enrichments:
                try:
                    deduped = self._apply_enrichment(deduped, enrichment)
                    enrichments_applied.append(enrichment)
                except Exception as e:
                    errors.append(f"Enrichment {enrichment}: {str(e)}")

        # Save to disk
        ds_path = self._dataset_dir / f"{symbol}_{tf}_v1"
        ds_path.mkdir(parents=True, exist_ok=True)

        file_path = ds_path / f"data.{request.format}"
        size_bytes = self._save_data(deduped, file_path, request.format)

        # Calculate checksum
        checksum = ""
        if file_path.exists():
            checksum = hashlib.md5(file_path.read_bytes()).hexdigest()

        # Build metadata
        columns = list(deduped[0].keys()) if deduped else []
        dates = [r.get("date", r.get("timestamp", "")) for r in deduped if r.get("date") or r.get("timestamp")]
        date_start = min(dates) if dates else ""
        date_end = max(dates) if dates else ""

        now = datetime.now(timezone.utc).isoformat()
        meta = DatasetMeta(
            dataset_id=ds_id,
            symbol=symbol,
            timeframe=tf,
            sources=sources_used,
            rows=len(deduped),
            columns=columns,
            date_range_start=str(date_start),
            date_range_end=str(date_end),
            enrichments=enrichments_applied,
            created_at=now,
            updated_at=now,
            size_bytes=size_bytes,
            format=request.format,
            path=str(file_path),
            version=1,
            checksum=checksum,
        )
        self._datasets[ds_id] = meta
        self._save_metadata()

        return {
            "success": True,
            "dataset": meta.to_dict(),
            "warnings": errors if errors else None,
        }

    # ── List / Status ────────────────────────────────────────────

    async def list_datasets(self) -> dict[str, Any]:
        """List all built datasets with metadata."""
        return {
            "datasets": [m.to_dict() for m in self._datasets.values()],
            "total_count": len(self._datasets),
            "total_size": sum(m.size_bytes for m in self._datasets.values()),
            "dataset_dir": str(self._dataset_dir),
        }

    async def dataset_status(self, available_keys: list[str]) -> dict[str, Any]:
        """
        Show what data you CAN build vs what you're missing,
        based on available API keys.
        """
        can_build = {
            "ohlcv_bars": {
                "available": any(
                    k in available_keys
                    for k in ["polygon", "alpha_vantage", "finnhub", "twelve_data", "yahoo_finance"]
                ),
                "providers": [
                    k for k in available_keys
                    if k in ["polygon", "alpha_vantage", "finnhub", "twelve_data", "yahoo_finance"]
                ],
                "timeframes": ["1min", "5min", "15min", "1h", "4h", "daily"],
            },
            "tick_data": {
                "available": "databento" in available_keys,
                "providers": ["databento"] if "databento" in available_keys else [],
                "note": "Requires Databento API key" if "databento" not in available_keys else "Available",
            },
            "options_flow": {
                "available": "unusual_whales" in available_keys,
                "providers": ["unusual_whales"] if "unusual_whales" in available_keys else [],
                "note": "Requires Unusual Whales API key" if "unusual_whales" not in available_keys else "Available",
            },
            "news_sentiment": {
                "available": any(k in available_keys for k in ["polygon", "finnhub"]),
                "providers": [k for k in available_keys if k in ["polygon", "finnhub"]],
            },
            "fundamentals": {
                "available": any(
                    k in available_keys
                    for k in ["polygon", "alpha_vantage", "intrinio", "yahoo_finance"]
                ),
                "providers": [
                    k for k in available_keys
                    if k in ["polygon", "alpha_vantage", "intrinio", "yahoo_finance"]
                ],
            },
            "economic_data": {
                "available": "quandl" in available_keys,
                "providers": ["quandl"] if "quandl" in available_keys else [],
                "note": "Requires Nasdaq Data Link key" if "quandl" not in available_keys else "Available",
            },
        }

        available_count = sum(1 for v in can_build.values() if v["available"])
        total = len(can_build)

        return {
            "capabilities": can_build,
            "available_categories": available_count,
            "total_categories": total,
            "coverage_pct": int((available_count / total) * 100),
            "configured_providers": available_keys,
        }

    # ── Enrichment ───────────────────────────────────────────────

    async def enrich_dataset(
        self, dataset_id: str, enrichments: list[str]
    ) -> dict[str, Any]:
        """Add enrichments to an existing dataset."""
        meta = self._datasets.get(dataset_id)
        if not meta:
            return {"error": f"Dataset {dataset_id} not found", "success": False}

        # Load existing data
        data = self._load_data(Path(meta.path), meta.format)
        if not data:
            return {"error": "Failed to load dataset", "success": False}

        applied = []
        for enrichment in enrichments:
            try:
                data = self._apply_enrichment(data, enrichment)
                applied.append(enrichment)
            except Exception as e:
                logger.warning(f"Enrichment {enrichment} failed: {e}")

        # Save updated data
        file_path = Path(meta.path)
        size_bytes = self._save_data(data, file_path, meta.format)

        # Update metadata
        meta.enrichments = list(set(meta.enrichments + applied))
        meta.columns = list(data[0].keys()) if data else meta.columns
        meta.rows = len(data)
        meta.size_bytes = size_bytes
        meta.updated_at = datetime.now(timezone.utc).isoformat()
        meta.version += 1
        self._save_metadata()

        return {
            "success": True,
            "enrichments_applied": applied,
            "dataset": meta.to_dict(),
        }

    # ── Export ────────────────────────────────────────────────────

    async def export_dataset(
        self,
        dataset_id: str,
        format: str = "parquet",
        train_test_split: float = 0.8,
        target_column: str = "close",
    ) -> dict[str, Any]:
        """Export dataset in ML-ready format with train/test split."""
        meta = self._datasets.get(dataset_id)
        if not meta:
            return {"error": f"Dataset {dataset_id} not found", "success": False}

        data = self._load_data(Path(meta.path), meta.format)
        if not data:
            return {"error": "Failed to load dataset", "success": False}

        # Sort by date
        data.sort(key=lambda r: r.get("date", r.get("timestamp", "")))

        # Split — time-based, no leakage
        split_idx = int(len(data) * train_test_split)
        train_data = data[:split_idx]
        test_data = data[split_idx:]

        # Export paths
        export_dir = Path(meta.path).parent / "ml_export"
        export_dir.mkdir(parents=True, exist_ok=True)

        train_path = export_dir / f"train.{format}"
        test_path = export_dir / f"test.{format}"

        train_size = self._save_data(train_data, train_path, format)
        test_size = self._save_data(test_data, test_path, format)

        return {
            "success": True,
            "train": {
                "path": str(train_path),
                "rows": len(train_data),
                "size_bytes": train_size,
                "date_range": f"{train_data[0].get('date', '?')} → {train_data[-1].get('date', '?')}" if train_data else "",
            },
            "test": {
                "path": str(test_path),
                "rows": len(test_data),
                "size_bytes": test_size,
                "date_range": f"{test_data[0].get('date', '?')} → {test_data[-1].get('date', '?')}" if test_data else "",
            },
            "split_ratio": train_test_split,
            "target_column": target_column,
            "feature_columns": [c for c in (data[0].keys() if data else []) if c != target_column],
            "anti_leakage": "Time-based split — no future data in training set",
        }

    # ── Internal Helpers ─────────────────────────────────────────

    def _get_available_providers(self) -> list[str]:
        """Get providers with configured API keys."""
        from ..byok.provider_registry import PROVIDER_REGISTRY

        available = []
        for name, meta in PROVIDER_REGISTRY.items():
            if not meta.requires_key:
                available.append(name)
                continue
            for env_var in meta.env_vars:
                if os.environ.get(env_var):
                    available.append(name)
                    break
        return available

    async def _fetch_from_provider(
        self, provider: str, symbol: str, timeframe: str,
        start_date: str | None, end_date: str | None,
    ) -> list[dict[str, Any]]:
        """Fetch data from a specific provider. Uses the V6 data_providers if available."""
        try:
            from ..data_providers.registry import DataProviderRegistry

            registry = DataProviderRegistry()
            provider_instance = registry.get_provider(provider)
            if provider_instance:
                bars = await provider_instance.get_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    start=start_date or "",
                    end=end_date or "",
                )
                return [
                    {
                        "date": b.timestamp,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                        "source": provider,
                    }
                    for b in bars
                ]
        except (ImportError, Exception) as e:
            logger.debug(f"V6 provider fetch failed for {provider}: {e}")

        return []

    def _normalize_data(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize column names and types across providers."""
        normalized = []
        for row in data:
            n = {}
            for key, value in row.items():
                clean_key = key.lower().strip()
                # Standardize common column names
                if clean_key in ("datetime", "timestamp", "time", "t"):
                    clean_key = "date"
                elif clean_key in ("o",):
                    clean_key = "open"
                elif clean_key in ("h",):
                    clean_key = "high"
                elif clean_key in ("l",):
                    clean_key = "low"
                elif clean_key in ("c", "adj close", "adj_close"):
                    clean_key = "close"
                elif clean_key in ("v", "vol"):
                    clean_key = "volume"
                n[clean_key] = value
            normalized.append(n)
        return normalized

    def _deduplicate(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate rows based on date + source priority."""
        seen: dict[str, dict[str, Any]] = {}
        # Priority: polygon > databento > finnhub > alpha_vantage > twelve_data > yahoo_finance
        priority = {
            "polygon": 0, "databento": 1, "finnhub": 2,
            "alpha_vantage": 3, "twelve_data": 4, "yahoo_finance": 5,
        }
        for row in data:
            key = str(row.get("date", ""))
            if key in seen:
                existing_prio = priority.get(seen[key].get("source", ""), 99)
                new_prio = priority.get(row.get("source", ""), 99)
                if new_prio < existing_prio:
                    seen[key] = row
            else:
                seen[key] = row
        return sorted(seen.values(), key=lambda r: str(r.get("date", "")))

    def _apply_enrichment(
        self, data: list[dict[str, Any]], enrichment: str
    ) -> list[dict[str, Any]]:
        """Apply an enrichment to the dataset."""
        if enrichment == EnrichmentType.TECHNICAL_INDICATORS.value:
            return self._add_technical_indicators(data)
        elif enrichment == EnrichmentType.REGIME_LABELS.value:
            return self._add_regime_labels(data)
        elif enrichment == EnrichmentType.CALENDAR_FEATURES.value:
            return self._add_calendar_features(data)
        elif enrichment == EnrichmentType.VOLUME_PROFILE.value:
            return self._add_volume_profile(data)
        return data

    def _add_technical_indicators(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add common technical indicators as features."""
        if len(data) < 26:
            return data

        closes = [float(r.get("close", 0)) for r in data]
        highs = [float(r.get("high", 0)) for r in data]
        lows = [float(r.get("low", 0)) for r in data]

        # SMA
        for period in (10, 20, 50):
            for i, row in enumerate(data):
                if i >= period - 1:
                    row[f"sma_{period}"] = sum(closes[i - period + 1:i + 1]) / period
                else:
                    row[f"sma_{period}"] = None

        # RSI (14-period)
        rsi_period = 14
        for i, row in enumerate(data):
            if i >= rsi_period:
                gains, losses = [], []
                for j in range(i - rsi_period + 1, i + 1):
                    change = closes[j] - closes[j - 1]
                    gains.append(max(change, 0))
                    losses.append(max(-change, 0))
                avg_gain = sum(gains) / rsi_period
                avg_loss = sum(losses) / rsi_period
                if avg_loss == 0:
                    row["rsi_14"] = 100.0
                else:
                    rs = avg_gain / avg_loss
                    row["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))
            else:
                row["rsi_14"] = None

        # ATR (14-period)
        for i, row in enumerate(data):
            if i >= rsi_period:
                trs = []
                for j in range(i - rsi_period + 1, i + 1):
                    tr = max(
                        highs[j] - lows[j],
                        abs(highs[j] - closes[j - 1]),
                        abs(lows[j] - closes[j - 1]),
                    )
                    trs.append(tr)
                row["atr_14"] = sum(trs) / rsi_period
            else:
                row["atr_14"] = None

        # Returns
        for i, row in enumerate(data):
            if i > 0 and closes[i - 1] != 0:
                row["return_1d"] = (closes[i] - closes[i - 1]) / closes[i - 1]
            else:
                row["return_1d"] = 0.0

        return data

    def _add_regime_labels(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add market regime labels based on SMA and volatility."""
        if len(data) < 50:
            return data

        closes = [float(r.get("close", 0)) for r in data]

        for i, row in enumerate(data):
            if i >= 50:
                sma_20 = sum(closes[i - 19:i + 1]) / 20
                sma_50 = sum(closes[i - 49:i + 1]) / 50
                recent_returns = [
                    (closes[j] - closes[j - 1]) / closes[j - 1]
                    for j in range(i - 19, i + 1) if closes[j - 1] != 0
                ]
                vol = (sum(r ** 2 for r in recent_returns) / len(recent_returns)) ** 0.5 if recent_returns else 0

                if closes[i] > sma_20 > sma_50:
                    regime = "bull"
                elif closes[i] < sma_20 < sma_50:
                    regime = "bear"
                else:
                    regime = "sideways"

                if vol > 0.02:
                    regime += "_high_vol"
                else:
                    regime += "_low_vol"

                row["regime"] = regime
            else:
                row["regime"] = None

        return data

    def _add_calendar_features(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add calendar-based features (day of week, month, etc.)."""
        for row in data:
            date_str = str(row.get("date", ""))
            try:
                if "T" in date_str:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                else:
                    dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
                row["day_of_week"] = dt.weekday()
                row["month"] = dt.month
                row["quarter"] = (dt.month - 1) // 3 + 1
                row["is_month_end"] = dt.day >= 25
                row["is_quarter_end"] = dt.month in (3, 6, 9, 12) and dt.day >= 25
            except (ValueError, TypeError):
                row["day_of_week"] = None
                row["month"] = None
                row["quarter"] = None
                row["is_month_end"] = None
                row["is_quarter_end"] = None
        return data

    def _add_volume_profile(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add volume-relative features."""
        volumes = [float(r.get("volume", 0)) for r in data]
        for i, row in enumerate(data):
            if i >= 20:
                avg_vol_20 = sum(volumes[i - 19:i + 1]) / 20
                if avg_vol_20 > 0:
                    row["volume_ratio_20"] = volumes[i] / avg_vol_20
                else:
                    row["volume_ratio_20"] = 1.0
            else:
                row["volume_ratio_20"] = None
        return data

    def _save_data(
        self, data: list[dict[str, Any]], path: Path, format: str
    ) -> int:
        """Save data to disk and return file size."""
        path.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            content = json.dumps(data, indent=2, default=str)
            path.write_text(content)
        elif format == "csv":
            if not data:
                path.write_text("")
                return 0
            headers = list(data[0].keys())
            lines = [",".join(headers)]
            for row in data:
                lines.append(",".join(str(row.get(h, "")) for h in headers))
            path.write_text("\n".join(lines))
        else:
            # Default to JSON if parquet deps not available
            try:
                import pandas as pd
                df = pd.DataFrame(data)
                df.to_parquet(path, index=False)
            except ImportError:
                path = path.with_suffix(".json")
                content = json.dumps(data, indent=2, default=str)
                path.write_text(content)

        return path.stat().st_size if path.exists() else 0

    def _load_data(self, path: Path, format: str) -> list[dict[str, Any]]:
        """Load data from disk."""
        if not path.exists():
            return []

        try:
            if format == "json" or path.suffix == ".json":
                return json.loads(path.read_text())
            elif format == "csv":
                lines = path.read_text().strip().split("\n")
                if len(lines) < 2:
                    return []
                headers = lines[0].split(",")
                return [
                    dict(zip(headers, line.split(",")))
                    for line in lines[1:]
                ]
            elif format == "parquet":
                import pandas as pd
                df = pd.read_parquet(path)
                return df.to_dict("records")
        except Exception as e:
            logger.warning(f"Failed to load data from {path}: {e}")
        return []
