"""CommunitySignalEngine — pub/sub signal bus with verification & consensus."""
from __future__ import annotations
import hashlib, logging, uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("algochains_mcp.community_signals")

class CommunitySignalEngine:
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    AI_GENERATED = "ai_generated"
    CONSENSUS = "consensus"

    def __init__(self):
        self._signals: list[dict[str, Any]] = []
        self._idx: dict[str, dict[str, Any]] = {}
        self._subs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._accuracy: dict[str, dict[str, Any]] = defaultdict(lambda: {"correct": 0, "total": 0, "score": 0.5})

    async def publish_signal(self, user_id: str, symbol: str, direction: str,
                             timeframe: str = "1h", entry_price: float | None = None,
                             stop_loss: float | None = None, take_profit: float | None = None,
                             confidence: float = 0.5, rationale: str = "",
                             category: str = "unverified", trade_hash: str | None = None) -> dict[str, Any]:
        sid = f"sig_{uuid.uuid4().hex[:12]}"
        vhash = None
        if trade_hash:
            vhash = hashlib.sha256(trade_hash.encode()).hexdigest()[:16]
            category = self.VERIFIED
        expiry_h = {"1min": 1, "5min": 2, "15min": 4, "1h": 24, "4h": 48, "daily": 168}.get(timeframe, 24)
        sig = {
            "signal_id": sid, "user_id": user_id, "symbol": symbol.upper(),
            "direction": direction.lower(), "timeframe": timeframe,
            "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
            "confidence": max(0.0, min(1.0, confidence)), "rationale": rationale,
            "category": category, "verification_hash": vhash,
            "published_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(hours=expiry_h)).isoformat(),
            "outcome": None, "consensus_score": None, "upvotes": 0, "downvotes": 0,
            "accuracy_at_publish": self._accuracy[user_id]["score"],
        }
        self._signals.append(sig)
        self._idx[sid] = sig
        return {"success": True, "signal": sig, "subscribers_notified": sum(len(s) for s in self._subs.values())}

    async def subscribe_signals(self, user_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        sub = {"id": f"sub_{uuid.uuid4().hex[:8]}", "user_id": user_id,
               "filters": filters or {}, "created_at": datetime.utcnow().isoformat(), "status": "active"}
        ch = filters.get("symbol", "all").upper() if filters else "all"
        self._subs[ch].append(sub)
        return {"success": True, "subscription": sub, "channel": ch}

    async def verify_signal(self, signal_id: str, trade_proof: dict[str, Any]) -> dict[str, Any]:
        sig = self._idx.get(signal_id)
        if not sig:
            return {"success": False, "error": f"Signal '{signal_id}' not found."}
        proof_str = f"{trade_proof.get('broker','')}:{trade_proof.get('order_id','')}:{trade_proof.get('fill_price','')}"
        sig["verification_hash"] = hashlib.sha256(proof_str.encode()).hexdigest()[:16]
        sig["category"] = self.VERIFIED
        sig["verified_at"] = datetime.utcnow().isoformat()
        return {"success": True, "signal_id": signal_id, "category": self.VERIFIED, "hash": sig["verification_hash"]}

    async def get_consensus(self, symbol: str, timeframe: str = "1h") -> dict[str, Any]:
        now = datetime.utcnow()
        recent = [s for s in self._signals if s["symbol"] == symbol.upper()
                  and s["timeframe"] == timeframe and datetime.fromisoformat(s["expires_at"]) > now]
        if not recent:
            return {"success": True, "symbol": symbol, "consensus": "neutral", "signals": 0, "score": 0.0}
        bull = sum(1 for s in recent if s["direction"] == "long")
        bear = sum(1 for s in recent if s["direction"] == "short")
        wb = sum(s["confidence"] * self._accuracy[s["user_id"]]["score"] for s in recent if s["direction"] == "long")
        wbr = sum(s["confidence"] * self._accuracy[s["user_id"]]["score"] for s in recent if s["direction"] == "short")
        tw = wb + wbr
        score = (wb - wbr) / tw if tw > 0 else 0
        consensus = "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral"
        return {"success": True, "symbol": symbol, "timeframe": timeframe, "consensus": consensus,
                "score": round(score, 4), "signals": len(recent), "bullish": bull, "bearish": bear}

    async def get_signal_accuracy(self, user_id: str) -> dict[str, Any]:
        acc = self._accuracy[user_id]
        total_sigs = sum(1 for s in self._signals if s["user_id"] == user_id)
        resolved = sum(1 for s in self._signals if s["user_id"] == user_id and s["outcome"])
        return {"success": True, "user_id": user_id, "accuracy_score": acc["score"],
                "correct": acc["correct"], "total": acc["total"],
                "total_signals": total_sigs, "resolved_signals": resolved}

    async def resolve_signal(self, signal_id: str, outcome: str) -> dict[str, Any]:
        sig = self._idx.get(signal_id)
        if not sig:
            return {"success": False, "error": f"Signal '{signal_id}' not found."}
        sig["outcome"] = outcome
        sig["resolved_at"] = datetime.utcnow().isoformat()
        uid = sig["user_id"]
        self._accuracy[uid]["total"] += 1
        if outcome in ("win", "correct"):
            self._accuracy[uid]["correct"] += 1
        t, c = self._accuracy[uid]["total"], self._accuracy[uid]["correct"]
        self._accuracy[uid]["score"] = round(c / t if t > 0 else 0.5, 4)
        return {"success": True, "signal_id": signal_id, "outcome": outcome, "new_accuracy": self._accuracy[uid]["score"]}
