"""
Proprietary Data Ingestion — AlgoChains MCP Server V22

Allows users to bring their own OHLCV history, pre-computed signals,
research documents (into Onyx), and custom strategy specs.

All paths are validated against real filesystem. No synthetic substitution.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_DIR = Path(os.getenv("ALGOCHAINS_STATE_DIR", "state"))
_INGESTION_REGISTRY = _STATE_DIR / "ingestion_registry.json"
_CUSTOM_DATA_DIR = _STATE_DIR / "custom_data"
_CUSTOM_STRATEGIES_DIR = _STATE_DIR / "custom_strategies"

# Regex for safe symbol/timeframe values used in path construction
import re as _re
_SAFE_SYMBOL_RE = _re.compile(r'^[A-Z0-9._\-]{1,20}$')
_SAFE_TIMEFRAME_RE = _re.compile(r'^[a-z0-9]{1,10}$')


def _sanitize_symbol(symbol: str) -> str:
    """Uppercase and validate symbol. Raises ValueError if unsafe."""
    s = symbol.upper().strip()
    if not _SAFE_SYMBOL_RE.match(s):
        raise ValueError(
            f"Symbol '{symbol}' contains invalid characters. "
            "Only A-Z, 0-9, '.', '_', '-' allowed (max 20 chars)."
        )
    return s


def _sanitize_timeframe(timeframe: str) -> str:
    """Validate timeframe. Raises ValueError if unsafe."""
    t = timeframe.lower().strip()
    if not _SAFE_TIMEFRAME_RE.match(t):
        raise ValueError(
            f"Timeframe '{timeframe}' contains invalid characters. "
            "Only a-z, 0-9 allowed (max 10 chars)."
        )
    return t


def _jail_check(path: Path, jail: Path) -> None:
    """Raise ValueError if resolved path escapes the jail directory."""
    try:
        path.resolve().relative_to(jail.resolve())
    except ValueError:
        raise ValueError(
            f"Path '{path}' resolves outside allowed directory '{jail}'. "
            "Symlink traversal or '../' detected."
        )


def _ensure_dirs() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _CUSTOM_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CUSTOM_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)


def _load_registry() -> dict:
    if _INGESTION_REGISTRY.exists():
        try:
            return json.loads(_INGESTION_REGISTRY.read_text())
        except Exception:
            return {}
    return {}


def _save_registry(reg: dict) -> None:
    _ensure_dirs()
    _INGESTION_REGISTRY.write_text(json.dumps(reg, indent=2, default=str))


# ---------------------------------------------------------------------------
# Tool 1: ingest_csv_data
# ---------------------------------------------------------------------------

REQUIRED_OHLCV_COLS = {"open", "high", "low", "close", "volume"}


def ingest_csv_data(
    file_path: str,
    symbol: str,
    timeframe: str,
    columns: dict[str, str] | None = None,
    date_column: str = "date",
    date_format: str = "%Y-%m-%d %H:%M:%S",
) -> dict[str, Any]:
    """
    Ingest a CSV file of OHLCV market data into AlgoChains.

    Args:
        file_path: Absolute path to the CSV file (must exist).
        symbol: Ticker symbol, e.g. "MNQ", "AAPL".
        timeframe: Bar timeframe, e.g. "1min", "5min", "1h", "1d".
        columns: Mapping of canonical names → CSV column headers.
                 e.g. {"open": "Open", "close": "Close"}.
                 Defaults to lowercase exact match.
        date_column: Name of the date/timestamp column.
        date_format: strptime format for parsing the date column.

    Returns:
        dict with status, rows_ingested, warnings, destination path.
    """
    _ensure_dirs()
    try:
        symbol = _sanitize_symbol(symbol)
        timeframe = _sanitize_timeframe(timeframe)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.suffix.lower() == ".csv":
        return {"success": False, "error": "File must be a .csv file"}

    col_map = {k: k for k in REQUIRED_OHLCV_COLS}
    col_map[date_column] = date_column
    if columns:
        for canonical, csv_col in columns.items():
            col_map[canonical] = csv_col

    warnings: list[str] = []
    rows_ok = 0
    rows_bad = 0

    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return {"success": False, "error": "CSV has no headers"}

            csv_fields = {h.strip() for h in reader.fieldnames}
            missing = [
                csv_col for canonical, csv_col in col_map.items()
                if csv_col not in csv_fields and canonical != date_column
            ]
            # Also check date column
            date_col_csv = col_map.get(date_column, date_column)
            if date_col_csv not in csv_fields:
                missing.append(date_col_csv)

            if missing:
                return {
                    "success": False,
                    "error": f"Missing required columns in CSV: {missing}. "
                             f"Available: {sorted(csv_fields)}",
                }

            clean_rows: list[dict] = []
            for i, row in enumerate(reader):
                try:
                    date_raw = row[date_col_csv].strip()
                    datetime.strptime(date_raw, date_format)
                    numeric = {
                        canonical: float(row[csv_col].strip())
                        for canonical, csv_col in col_map.items()
                        if canonical != date_column
                    }
                    clean_rows.append({date_column: date_raw, **numeric})
                    rows_ok += 1
                except (ValueError, KeyError) as e:
                    rows_bad += 1
                    if rows_bad <= 3:
                        warnings.append(f"Row {i + 2} skipped: {e}")

    except Exception as e:
        return {"success": False, "error": f"Failed reading CSV: {e}"}

    if rows_ok == 0:
        return {"success": False, "error": "No valid rows found in CSV file"}

    dest_dir = _CUSTOM_DATA_DIR / symbol / timeframe
    try:
        _jail_check(dest_dir, _CUSTOM_DATA_DIR)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    dest_file = dest_dir / f"{symbol}_{timeframe}_{ts}.json"
    dest_file.write_text(json.dumps(clean_rows, indent=2))

    reg = _load_registry()
    key = f"csv_{symbol.upper()}_{timeframe}"
    reg[key] = {
        "type": "ohlcv_csv",
        "symbol": symbol,
        "timeframe": timeframe,
        "source_file": str(path),
        "destination": str(dest_file),
        "rows": rows_ok,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_registry(reg)

    return {
        "success": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "rows_ingested": rows_ok,
        "rows_skipped": rows_bad,
        "destination": str(dest_file),
        "warnings": warnings[:10],
        "note": "Data is now available for backtests via run_backtest(data_source='custom')",
    }


# ---------------------------------------------------------------------------
# Tool 2: ingest_json_signals
# ---------------------------------------------------------------------------

VALID_SIGNAL_TYPES = {"entry_exit", "features", "labels", "regime"}


def ingest_json_signals(
    file_path: str,
    signal_type: str,
    symbol: str,
) -> dict[str, Any]:
    """
    Ingest a JSON file of pre-computed signals, features, labels, or regime tags.

    Args:
        file_path: Absolute path to the JSON file (must exist).
        signal_type: One of: "entry_exit" | "features" | "labels" | "regime".
        symbol: Ticker symbol the signals are for.

    Returns:
        dict with status, records_ingested, destination path.
    """
    _ensure_dirs()
    if signal_type not in VALID_SIGNAL_TYPES:
        return {
            "success": False,
            "error": f"signal_type must be one of: {sorted(VALID_SIGNAL_TYPES)}",
        }
    try:
        symbol = _sanitize_symbol(symbol)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if not path.suffix.lower() == ".json":
        return {"success": False, "error": "File must be a .json file"}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON: {e}"}
    except Exception as e:
        return {"success": False, "error": f"Cannot read file: {e}"}

    if isinstance(raw, dict):
        records = [raw]
    elif isinstance(raw, list):
        records = raw
    else:
        return {"success": False, "error": "JSON must be an object or array of objects"}

    if len(records) == 0:
        return {"success": False, "error": "JSON file contains no records"}

    dest_dir = _CUSTOM_DATA_DIR / symbol / "signals" / signal_type
    try:
        _jail_check(dest_dir, _CUSTOM_DATA_DIR)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    dest_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    dest_file = dest_dir / f"{symbol}_{signal_type}_{ts}.json"
    dest_file.write_text(json.dumps(records, indent=2))

    reg = _load_registry()
    key = f"signals_{symbol.upper()}_{signal_type}"
    reg[key] = {
        "type": "json_signals",
        "signal_type": signal_type,
        "symbol": symbol,
        "source_file": str(path),
        "destination": str(dest_file),
        "records": len(records),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_registry(reg)

    return {
        "success": True,
        "symbol": symbol,
        "signal_type": signal_type,
        "records_ingested": len(records),
        "destination": str(dest_file),
        "note": f"Signals available for ML training via train_model(signal_source='custom', symbol='{symbol.upper()}')",
    }


# ---------------------------------------------------------------------------
# Tool 3: connect_onyx_docs
# ---------------------------------------------------------------------------

VALID_DOC_TYPES = {"strategy_research", "blueprint", "backtest", "whitepaper", "general"}


def connect_onyx_docs(
    doc_paths: list[str],
    doc_type: str,
    onyx_url: str | None = None,
    onyx_key: str | None = None,
) -> dict[str, Any]:
    """
    Index local research documents into the Onyx RAG knowledge base.

    Supports .txt, .md, .pdf, .json files. Directories are recursively
    expanded. Real Onyx API call is attempted; fails loudly if unreachable.

    Args:
        doc_paths: List of absolute file or directory paths.
        doc_type: One of: "strategy_research" | "blueprint" | "backtest" |
                  "whitepaper" | "general".
        onyx_url: Override ONYX_API_URL env var.
        onyx_key: Override ONYX_API_KEY env var.

    Returns:
        dict with status, files_submitted, files_failed, onyx_job_ids.
    """
    _ensure_dirs()

    if doc_type not in VALID_DOC_TYPES:
        return {
            "success": False,
            "error": f"doc_type must be one of: {sorted(VALID_DOC_TYPES)}",
        }

    url = onyx_url or os.getenv("ONYX_API_URL", "http://100.89.114.31:8085")
    key = onyx_key or os.getenv("ONYX_API_KEY", "")

    # Expand all paths to individual files
    files_to_index: list[Path] = []
    not_found: list[str] = []

    for p in doc_paths:
        path = Path(p)
        if not path.exists():
            not_found.append(str(p))
            continue
        if path.is_dir():
            for ext in ("*.txt", "*.md", "*.pdf", "*.json"):
                files_to_index.extend(path.rglob(ext))
        elif path.is_file():
            files_to_index.append(path)

    if not_found:
        return {
            "success": False,
            "error": f"Paths not found: {not_found}. Provide real absolute paths.",
        }

    if not files_to_index:
        return {"success": False, "error": "No indexable files found (.txt/.md/.pdf/.json)"}

    # Attempt real Onyx API call
    try:
        import httpx
        submitted: list[str] = []
        failed: list[dict] = []
        job_ids: list[str] = []

        for fpath in files_to_index:
            try:
                with fpath.open("rb") as fh:
                    resp = httpx.post(
                        f"{url.rstrip('/')}/api/connector/file/ingest",
                        headers={"Authorization": f"Bearer {key}"} if key else {},
                        files={"file": (fpath.name, fh, "application/octet-stream")},
                        data={"doc_set": doc_type, "source": "algochains_mcp"},
                        timeout=30.0,
                    )
                if resp.status_code in (200, 201):
                    submitted.append(str(fpath))
                    try:
                        job_ids.append(resp.json().get("job_id", ""))
                    except Exception:
                        pass
                else:
                    failed.append({"file": str(fpath), "status": resp.status_code, "body": resp.text[:200]})
            except Exception as e:
                failed.append({"file": str(fpath), "error": str(e)})

    except ImportError:
        return {"success": False, "error": "httpx not installed — run: pip install httpx"}
    except Exception as e:
        return {"success": False, "error": f"Cannot reach Onyx at {url}: {e}. Is the desktop online and Onyx running?"}

    reg = _load_registry()
    ts_key = f"onyx_{doc_type}_{int(time.time())}"
    reg[ts_key] = {
        "type": "onyx_docs",
        "doc_type": doc_type,
        "files_submitted": submitted,
        "files_failed": failed,
        "onyx_url": url,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_registry(reg)

    return {
        "success": len(submitted) > 0,
        "doc_type": doc_type,
        "files_submitted": len(submitted),
        "files_failed": len(failed),
        "failures": failed[:5],
        "job_ids": [j for j in job_ids if j],
        "note": "Documents will be searchable via onyx_ask() and onyx_search() once indexed (typically <60s)",
    }


# ---------------------------------------------------------------------------
# Tool 4: register_strategy
# ---------------------------------------------------------------------------

VALID_ASSET_CLASSES = {"futures", "equities", "forex", "crypto", "options"}
VALID_TIMEFRAMES = {"1min", "3min", "5min", "10min", "15min", "30min", "1h", "4h", "1d", "1w"}


def register_strategy(
    name: str,
    asset_class: str,
    timeframe: str,
    symbols: list[str],
    spec_path: str,
    description: str = "",
    author: str = "",
) -> dict[str, Any]:
    """
    Register a custom strategy spec with the AlgoChains platform.

    The spec JSON must contain at minimum: entry_rules, exit_rules.
    The strategy will be available for backtesting via run_backtest().

    Args:
        name: Human-readable strategy name.
        asset_class: One of: "futures" | "equities" | "forex" | "crypto" | "options".
        timeframe: Bar timeframe (e.g. "5min", "1h", "1d").
        symbols: List of ticker symbols this strategy trades.
        spec_path: Absolute path to strategy spec JSON file.
        description: Optional description.
        author: Optional author name.

    Returns:
        dict with status, strategy_id, destination path.
    """
    _ensure_dirs()

    if not name.strip():
        return {"success": False, "error": "name cannot be empty"}
    if asset_class not in VALID_ASSET_CLASSES:
        return {"success": False, "error": f"asset_class must be one of: {sorted(VALID_ASSET_CLASSES)}"}
    if timeframe not in VALID_TIMEFRAMES:
        return {"success": False, "error": f"timeframe must be one of: {sorted(VALID_TIMEFRAMES)}"}
    if not symbols:
        return {"success": False, "error": "symbols list cannot be empty"}
    try:
        clean_symbols = [_sanitize_symbol(s) for s in symbols]
    except ValueError as e:
        return {"success": False, "error": str(e)}

    spec = Path(spec_path)
    if not spec.exists():
        return {"success": False, "error": f"spec_path not found: {spec_path}"}
    if not spec.suffix.lower() == ".json":
        return {"success": False, "error": "spec_path must be a .json file"}

    try:
        spec_data = json.loads(spec.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid JSON in spec: {e}"}

    if "entry_rules" not in spec_data or "exit_rules" not in spec_data:
        return {
            "success": False,
            "error": "Strategy spec must contain 'entry_rules' and 'exit_rules' keys",
        }

    strategy_id = f"{name.lower().replace(' ', '_')}_{int(time.time())}"
    safe_id = "".join(c if c.isalnum() or c == "_" else "_" for c in strategy_id)

    manifest = {
        "strategy_id": safe_id,
        "name": name,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "symbols": clean_symbols,
        "description": description,
        "author": author,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "spec": spec_data,
    }

    dest_file = _CUSTOM_STRATEGIES_DIR / f"{safe_id}.json"
    dest_file.write_text(json.dumps(manifest, indent=2))

    reg = _load_registry()
    reg[f"strategy_{safe_id}"] = {
        "type": "custom_strategy",
        "strategy_id": safe_id,
        "name": name,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "symbols": clean_symbols,
        "destination": str(dest_file),
        "registered_at": manifest["registered_at"],
    }
    _save_registry(reg)

    return {
        "success": True,
        "strategy_id": safe_id,
        "name": name,
        "asset_class": asset_class,
        "timeframe": timeframe,
        "symbols": clean_symbols,
        "destination": str(dest_file),
        "note": f"Strategy available via run_backtest(strategy_id='{safe_id}')",
    }


# ---------------------------------------------------------------------------
# Tool 5: list_ingested_data
# ---------------------------------------------------------------------------

def list_ingested_data() -> dict[str, Any]:
    """
    List all custom data, signals, and strategies that have been ingested.

    Returns:
        dict with categorized inventory of all ingested items.
    """
    reg = _load_registry()
    if not reg:
        return {
            "success": True,
            "total": 0,
            "items": [],
            "note": "No custom data ingested yet. Use ingest_csv_data, ingest_json_signals, connect_onyx_docs, or register_strategy.",
        }

    ohlcv = [v for v in reg.values() if v.get("type") == "ohlcv_csv"]
    signals = [v for v in reg.values() if v.get("type") == "json_signals"]
    onyx = [v for v in reg.values() if v.get("type") == "onyx_docs"]
    strategies = [v for v in reg.values() if v.get("type") == "custom_strategy"]

    return {
        "success": True,
        "total": len(reg),
        "ohlcv_datasets": len(ohlcv),
        "signal_files": len(signals),
        "onyx_ingestions": len(onyx),
        "custom_strategies": len(strategies),
        "inventory": {
            "ohlcv": [
                {"symbol": v["symbol"], "timeframe": v["timeframe"], "rows": v.get("rows", "?"), "ingested_at": v["ingested_at"]}
                for v in ohlcv
            ],
            "signals": [
                {"symbol": v["symbol"], "signal_type": v["signal_type"], "records": v.get("records", "?"), "ingested_at": v["ingested_at"]}
                for v in signals
            ],
            "onyx_docs": [
                {"doc_type": v["doc_type"], "files_submitted": v.get("files_submitted", []), "ingested_at": v["ingested_at"]}
                for v in onyx
            ],
            "strategies": [
                {"strategy_id": v["strategy_id"], "name": v["name"], "asset_class": v["asset_class"], "symbols": v["symbols"], "registered_at": v["registered_at"]}
                for v in strategies
            ],
        },
    }
