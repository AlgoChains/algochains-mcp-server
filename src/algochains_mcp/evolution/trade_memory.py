"""
Episodic Trade Memory — vectorized store of past trades and lessons.

Every completed trade is recorded with its outcome, market context,
and extracted lessons. Similar historical setups can be retrieved to
inform current decisions.

Storage: SQLite + numpy embeddings at ~/.algochains/trade_memory.db
Optional: chromadb for semantic search (install with [chromadb] extra)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TradeEpisode:
    episode_id: str
    symbol: str
    side: str                     # "long" | "short"
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    pnl_pct: float
    holding_period_seconds: float
    market_regime: str            # "bull" | "bear" | "neutral" | "volatile"
    signals_used: list[str]       # e.g. ["vwap_dev", "gex_positive", "kelly_1.2x"]
    strategy_id: str
    broker: str
    lessons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "qty": self.qty,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "holding_period_minutes": round(self.holding_period_seconds / 60, 1),
            "market_regime": self.market_regime,
            "signals_used": self.signals_used,
            "strategy_id": self.strategy_id,
            "broker": self.broker,
            "lessons": self.lessons,
            "tags": self.tags,
            "timestamp": self.timestamp,
            "outcome": "win" if self.pnl > 0 else "loss",
        }

    @classmethod
    def from_row(cls, row: tuple) -> "TradeEpisode":
        (ep_id, symbol, side, entry, exit_, qty, pnl, pnl_pct, holding,
         regime, signals_json, strategy_id, broker, lessons_json, tags_json,
         ts, meta_json) = row
        return cls(
            episode_id=ep_id,
            symbol=symbol,
            side=side,
            entry_price=entry,
            exit_price=exit_,
            qty=qty,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_period_seconds=holding,
            market_regime=regime,
            signals_used=json.loads(signals_json or "[]"),
            strategy_id=strategy_id or "",
            broker=broker or "",
            lessons=json.loads(lessons_json or "[]"),
            tags=json.loads(tags_json or "[]"),
            timestamp=ts,
            metadata=json.loads(meta_json or "{}"),
        )


class TradeMemory:
    """
    Persistent episodic memory of past trades.

    Core operations:
      record(episode)         → store outcome
      query_similar(state)    → find similar past setups
      get_lessons(regime)     → extract regime-specific lessons
      performance_by_regime() → win rate / avg PnL per regime
    """

    DB_PATH = Path.home() / ".algochains" / "trade_memory.db"

    def __init__(self) -> None:
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._chromadb = None
        self._try_load_chromadb()

    def _init_db(self) -> None:
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    qty REAL,
                    pnl REAL,
                    pnl_pct REAL,
                    holding_period_seconds REAL,
                    market_regime TEXT,
                    signals_json TEXT,
                    strategy_id TEXT,
                    broker TEXT,
                    lessons_json TEXT,
                    tags_json TEXT,
                    timestamp REAL,
                    meta_json TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON episodes(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_regime ON episodes(market_regime)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy ON episodes(strategy_id)")
            conn.commit()

    def _try_load_chromadb(self) -> None:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(Path.home() / ".algochains" / "chroma"))
            self._chromadb = client.get_or_create_collection("trade_episodes")
        except ImportError:
            pass

    # ── Core Operations ───────────────────────────────────────────────

    def record(self, episode: TradeEpisode) -> str:
        """Persist a trade episode. Returns episode_id."""
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                episode.episode_id, episode.symbol, episode.side,
                episode.entry_price, episode.exit_price, episode.qty,
                episode.pnl, episode.pnl_pct, episode.holding_period_seconds,
                episode.market_regime,
                json.dumps(episode.signals_used),
                episode.strategy_id, episode.broker,
                json.dumps(episode.lessons),
                json.dumps(episode.tags),
                episode.timestamp,
                json.dumps(episode.metadata),
            ))
            conn.commit()

        # Also index in chromadb for semantic search if available
        if self._chromadb:
            try:
                doc = (
                    f"{episode.symbol} {episode.side} {episode.market_regime} "
                    f"signals={','.join(episode.signals_used)} "
                    f"pnl_pct={episode.pnl_pct:.1f}% "
                    f"lessons={' '.join(episode.lessons)}"
                )
                self._chromadb.upsert(
                    ids=[episode.episode_id],
                    documents=[doc],
                    metadatas=[{"symbol": episode.symbol, "regime": episode.market_regime, "pnl": episode.pnl}],
                )
            except Exception:
                pass

        return episode.episode_id

    def query_similar(
        self,
        symbol: str,
        regime: str,
        signals: list[str],
        top_k: int = 5,
    ) -> list[TradeEpisode]:
        """Find similar historical setups using SQL + optional semantic search."""
        with sqlite3.connect(self.DB_PATH) as conn:
            rows = conn.execute("""
                SELECT * FROM episodes
                WHERE symbol = ? AND market_regime = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, regime, top_k * 3)).fetchall()

        episodes = [TradeEpisode.from_row(r) for r in rows]

        # Score by signal overlap
        def signal_overlap(ep: TradeEpisode) -> float:
            if not signals or not ep.signals_used:
                return 0.0
            overlap = len(set(signals) & set(ep.signals_used))
            return overlap / max(len(signals), len(ep.signals_used))

        episodes.sort(key=signal_overlap, reverse=True)
        return episodes[:top_k]

    def get_lessons(self, regime: str, symbol: str | None = None, limit: int = 10) -> list[str]:
        """Extract lessons learned for a given regime (and optionally symbol)."""
        query = "SELECT lessons_json FROM episodes WHERE market_regime = ?"
        params: list = [regime]
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY timestamp DESC LIMIT 100"

        with sqlite3.connect(self.DB_PATH) as conn:
            rows = conn.execute(query, params).fetchall()

        all_lessons: list[str] = []
        for row in rows:
            lessons = json.loads(row[0] or "[]")
            all_lessons.extend(lessons)

        # Deduplicate and return most frequent
        seen: dict[str, int] = {}
        for l in all_lessons:
            seen[l] = seen.get(l, 0) + 1
        sorted_lessons = sorted(seen.items(), key=lambda x: x[1], reverse=True)
        return [lesson for lesson, _ in sorted_lessons[:limit]]

    def performance_by_regime(self) -> dict[str, dict[str, Any]]:
        """Win rate and avg PnL grouped by market regime."""
        with sqlite3.connect(self.DB_PATH) as conn:
            rows = conn.execute("""
                SELECT market_regime,
                       COUNT(*) as total,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(pnl) as avg_pnl,
                       AVG(pnl_pct) as avg_pnl_pct,
                       SUM(pnl) as total_pnl
                FROM episodes
                GROUP BY market_regime
            """).fetchall()

        result: dict[str, dict[str, Any]] = {}
        for regime, total, wins, avg_pnl, avg_pnl_pct, total_pnl in rows:
            result[regime] = {
                "trade_count": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "avg_pnl": round(avg_pnl, 2),
                "avg_pnl_pct": round(avg_pnl_pct, 2),
                "total_pnl": round(total_pnl, 2),
            }
        return result

    def performance_by_strategy(self, strategy_id: str | None = None) -> dict[str, Any]:
        """Performance metrics per strategy."""
        query = "SELECT strategy_id, COUNT(*), SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), AVG(pnl), AVG(pnl_pct), SUM(pnl) FROM episodes"
        params: list = []
        if strategy_id:
            query += " WHERE strategy_id = ?"
            params.append(strategy_id)
        query += " GROUP BY strategy_id ORDER BY SUM(pnl) DESC"

        with sqlite3.connect(self.DB_PATH) as conn:
            rows = conn.execute(query, params).fetchall()

        results = []
        for sid, total, wins, avg_pnl, avg_pct, total_pnl in rows:
            results.append({
                "strategy_id": sid,
                "trade_count": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "avg_pnl": round(avg_pnl, 2),
                "avg_pnl_pct": round(avg_pct, 2),
                "total_pnl": round(total_pnl, 2),
            })
        return {"strategies": results, "total_strategies": len(results)}

    def add_lesson(self, episode_id: str, lesson: str) -> bool:
        """Append a lesson to an existing episode."""
        with sqlite3.connect(self.DB_PATH) as conn:
            row = conn.execute("SELECT lessons_json FROM episodes WHERE episode_id = ?", (episode_id,)).fetchone()
            if not row:
                return False
            lessons = json.loads(row[0] or "[]")
            if lesson not in lessons:
                lessons.append(lesson)
            conn.execute("UPDATE episodes SET lessons_json = ? WHERE episode_id = ?",
                         (json.dumps(lessons), episode_id))
            conn.commit()
        return True

    def stats(self) -> dict[str, Any]:
        with sqlite3.connect(self.DB_PATH) as conn:
            total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            wins = conn.execute("SELECT COUNT(*) FROM episodes WHERE pnl > 0").fetchone()[0]
            total_pnl = conn.execute("SELECT SUM(pnl) FROM episodes").fetchone()[0] or 0
        return {
            "total_episodes": total,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "chromadb_enabled": self._chromadb is not None,
            "db_path": str(self.DB_PATH),
        }


_trade_memory: TradeMemory | None = None


def get_trade_memory() -> TradeMemory:
    global _trade_memory
    if _trade_memory is None:
        _trade_memory = TradeMemory()
    return _trade_memory
