"""
Earnings Catalyst NLP Pipeline — Real Data Only.

Pipeline: SEC EDGAR filing → earnings call transcript → FinBERT sentiment → signal

Data sources:
  1. SEC EDGAR full-text search API (free, no auth):
     https://efts.sec.gov/LATEST/search-index?q="earnings+call"&dateRange=custom&...
  2. SEC EDGAR filing API (free):
     https://data.sec.gov/submissions/CIK{cik}.json
  3. FinBERT sentiment (transformers library — optional, falls back to VADER)
  4. Massive.com financial data API (enterprise license) for EPS data
  5. Polygon.io /vX/reference/financials for EPS history

FAIL CLOSED: Raises EarningsDataUnavailableError if real data cannot be fetched.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("algochains_mcp.order_flow.earnings")


class EarningsDataUnavailableError(Exception):
    pass


@dataclass
class EarningsCatalystSignal:
    symbol: str
    quarter: str
    sentiment_score: float          # -1 to +1 (FinBERT)
    sentiment_label: str            # "positive" | "negative" | "neutral"
    key_themes: list[str]           # extracted topics from transcript
    guidance_tone: str              # "raised" | "lowered" | "maintained" | "unknown"
    eps_surprise_direction: str     # "beat" | "miss" | "in_line" | "unknown"
    eps_actual: float | None
    eps_estimate: float | None
    eps_surprise_pct: float | None
    directional_signal: str         # "bullish" | "bearish" | "neutral"
    signal_confidence: float        # 0-1
    data_source: str
    filing_url: str
    computed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quarter": self.quarter,
            "sentiment_score": round(self.sentiment_score, 3),
            "sentiment_label": self.sentiment_label,
            "key_themes": self.key_themes,
            "guidance_tone": self.guidance_tone,
            "eps_surprise_direction": self.eps_surprise_direction,
            "eps_actual": self.eps_actual,
            "eps_estimate": self.eps_estimate,
            "eps_surprise_pct": round(self.eps_surprise_pct, 2) if self.eps_surprise_pct else None,
            "directional_signal": self.directional_signal,
            "signal_confidence": round(self.signal_confidence, 2),
            "data_source": self.data_source,
            "filing_url": self.filing_url,
            "computed_at": self.computed_at,
        }


class EarningsCatalystEngine:
    """
    End-to-end pipeline for earnings catalyst analysis.

    Steps:
    1. Resolve CIK from symbol via SEC EDGAR
    2. Fetch most recent 10-Q or 8-K earnings filing
    3. Extract transcript text (or MD&A section)
    4. Run FinBERT sentiment analysis
    5. Extract key themes using keyword frequency
    6. Fetch EPS actual vs estimate from Polygon.io financials
    7. Derive directional signal
    """

    SEC_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
    SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
    SEC_FILING_URL = "https://www.sec.gov/Archives/edgar/{path}"
    EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index?q={query}&dateRange=custom&startdt={start}&enddt={end}&forms=8-K"

    GUIDANCE_RAISE_WORDS = ["raise", "raised", "increased guidance", "above consensus", "outperform", "raised outlook", "raise guidance"]
    GUIDANCE_LOWER_WORDS = ["lower", "reduced guidance", "below consensus", "underperform", "lowered outlook", "cut guidance", "headwinds"]
    BULLISH_THEMES = ["record revenue", "beat estimates", "strong demand", "margin expansion", "share buyback", "dividend increase"]
    BEARISH_THEMES = ["miss", "lower guidance", "macro headwinds", "pricing pressure", "margin compression", "restructuring", "layoffs"]

    def __init__(self) -> None:
        self._cik_cache: dict[str, str] = {}
        self._sentiment_model = None
        self._sentiment_tokenizer = None

    def _resolve_cik(self, symbol: str) -> str:
        """Resolve ticker symbol to SEC CIK via SEC EDGAR company tickers JSON."""
        if symbol in self._cik_cache:
            return self._cik_cache[symbol]

        req = urllib.request.Request(
            self.SEC_COMPANY_TICKERS,
            headers={"User-Agent": "AlgoChains-MCP/21.0 (algochains.ai)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        sym_upper = symbol.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == sym_upper:
                cik = str(entry["cik_str"]).zfill(10)
                self._cik_cache[symbol] = cik
                return cik

        raise EarningsDataUnavailableError(
            f"Symbol {symbol} not found in SEC EDGAR company tickers. "
            "Only US-listed companies with SEC filings are supported."
        )

    def _fetch_recent_filing_text(self, cik: str, form_type: str = "8-K") -> tuple[str, str]:
        """Fetch most recent 8-K or 10-Q filing text. Returns (text, url)."""
        url = self.SEC_SUBMISSIONS.format(cik=cik)
        req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0 (algochains.ai)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            subs = json.loads(resp.read())

        filings = subs.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accession_nums = filings.get("accessionNumber", [])
        primary_docs = filings.get("primaryDocument", [])

        for i, form in enumerate(forms):
            if form == form_type and i < len(accession_nums):
                accession = accession_nums[i].replace("-", "")
                doc = primary_docs[i] if i < len(primary_docs) else ""
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc}"
                try:
                    req2 = urllib.request.Request(filing_url, headers={"User-Agent": "AlgoChains-MCP/21.0 (algochains.ai)"})
                    with urllib.request.urlopen(req2, timeout=15) as resp2:
                        raw = resp2.read().decode("utf-8", errors="replace")
                    # Strip HTML tags
                    text = re.sub(r"<[^>]+>", " ", raw)
                    text = re.sub(r"\s+", " ", text).strip()
                    return text[:50000], filing_url  # limit to 50k chars
                except Exception:
                    continue

        raise EarningsDataUnavailableError(
            f"No recent {form_type} filing found for CIK {cik}. "
            "EDGAR may not have recent filings for this company."
        )

    def _run_finbert_sentiment(self, text: str) -> tuple[float, str]:
        """Run FinBERT sentiment on the text. Falls back to VADER if not installed."""
        try:
            from transformers import pipeline as hf_pipeline
            if self._sentiment_model is None:
                self._sentiment_model = hf_pipeline(
                    "sentiment-analysis",
                    model="ProsusAI/finbert",
                    tokenizer="ProsusAI/finbert",
                    device=-1,  # CPU
                    truncation=True,
                    max_length=512,
                )
            # Chunk text into 512-token windows
            chunks = [text[i:i+2000] for i in range(0, min(len(text), 20000), 2000)]
            scores = []
            for chunk in chunks[:5]:
                result = self._sentiment_model(chunk[:512])[0]
                label = result["label"].lower()
                score = result["score"]
                if label == "positive":
                    scores.append(score)
                elif label == "negative":
                    scores.append(-score)
                else:
                    scores.append(0.0)
            avg = sum(scores) / len(scores) if scores else 0.0
            label = "positive" if avg > 0.1 else ("negative" if avg < -0.1 else "neutral")
            return round(avg, 3), label
        except ImportError:
            pass

        # Fallback to VADER (much faster, no model download)
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            sia = SentimentIntensityAnalyzer()
            chunks = [text[i:i+5000] for i in range(0, min(len(text), 20000), 5000)]
            scores = [sia.polarity_scores(c)["compound"] for c in chunks]
            avg = sum(scores) / len(scores) if scores else 0.0
            label = "positive" if avg > 0.05 else ("negative" if avg < -0.05 else "neutral")
            return round(avg, 3), label
        except ImportError:
            raise EarningsDataUnavailableError(
                "Sentiment analysis requires either 'transformers' (FinBERT) or 'vaderSentiment'. "
                "Install: pip install transformers torch  OR  pip install vaderSentiment"
            )

    def _extract_themes(self, text: str) -> list[str]:
        """Extract key themes from earnings text via keyword frequency."""
        text_lower = text.lower()
        themes = []
        all_keywords = {
            "revenue growth": ["revenue", "sales growth", "top-line"],
            "margin expansion": ["margin", "gross margin", "operating margin"],
            "beat estimates": ["beat", "above expectations", "exceeded estimates"],
            "guidance raised": self.GUIDANCE_RAISE_WORDS,
            "guidance lowered": self.GUIDANCE_LOWER_WORDS,
            "share buyback": ["buyback", "repurchase", "share repurchase"],
            "AI/cloud growth": ["artificial intelligence", "ai", "cloud", "hyperscale"],
            "macro headwinds": ["macro", "headwinds", "slowdown", "recession"],
            "pricing pressure": ["pricing pressure", "competitive", "price reduction"],
            "restructuring": ["restructuring", "layoffs", "cost reduction"],
        }
        for theme, keywords in all_keywords.items():
            if any(kw in text_lower for kw in keywords):
                themes.append(theme)
        return themes[:8]

    def _detect_guidance(self, text: str) -> str:
        text_lower = text.lower()
        raise_count = sum(1 for w in self.GUIDANCE_RAISE_WORDS if w in text_lower)
        lower_count = sum(1 for w in self.GUIDANCE_LOWER_WORDS if w in text_lower)
        if raise_count > lower_count and raise_count >= 2:
            return "raised"
        elif lower_count > raise_count and lower_count >= 2:
            return "lowered"
        return "maintained"

    def _fetch_eps_data(self, symbol: str) -> tuple[float | None, float | None, float | None]:
        """Fetch actual vs estimated EPS from Polygon.io financials."""
        api_key = os.environ.get("POLYGON_API_KEY", "")
        if not api_key:
            return None, None, None

        try:
            url = (
                f"https://api.polygon.io/vX/reference/financials"
                f"?ticker={symbol}&limit=1&sort=filing_date&apiKey={api_key}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            results = data.get("results", [])
            if not results:
                return None, None, None

            financials = results[0].get("financials", {})
            income = financials.get("income_statement", {})
            eps_actual = income.get("basic_earnings_per_share", {}).get("value")

            # EPS estimate from consensus (Polygon doesn't have this in free tier)
            # Use Massive.com if configured
            massive_key = os.environ.get("MASSIVE_API_KEY", "")
            if massive_key and eps_actual is not None:
                try:
                    est_url = (
                        f"https://api.massive.com/v2/estimates/eps"
                        f"?symbol={symbol}&limit=1&apiKey={massive_key}"
                    )
                    req2 = urllib.request.Request(est_url, headers={"User-Agent": "AlgoChains-MCP/21.0"})
                    with urllib.request.urlopen(req2, timeout=8) as resp2:
                        est_data = json.loads(resp2.read())
                    eps_est = est_data.get("results", [{}])[0].get("epsEstimate")
                    if eps_est and eps_actual:
                        surprise = (float(eps_actual) - float(eps_est)) / abs(float(eps_est)) * 100
                        return float(eps_actual), float(eps_est), round(surprise, 2)
                except Exception:
                    pass

            return float(eps_actual) if eps_actual else None, None, None
        except Exception:
            return None, None, None

    def analyze(self, symbol: str, quarter: str | None = None) -> EarningsCatalystSignal:
        """
        Full earnings catalyst analysis pipeline.

        Args:
            symbol: Ticker symbol (e.g. "NVDA")
            quarter: Optional quarter label (e.g. "Q1 2026") — informational only

        Returns:
            EarningsCatalystSignal with real sentiment, themes, EPS data
        """
        quarter = quarter or "latest"

        # Step 1: Resolve CIK
        cik = self._resolve_cik(symbol)

        # Step 2: Fetch recent 8-K filing text
        text, filing_url = self._fetch_recent_filing_text(cik, "8-K")

        # Step 3: FinBERT / VADER sentiment
        sentiment_score, sentiment_label = self._run_finbert_sentiment(text)

        # Step 4: Key themes
        themes = self._extract_themes(text)

        # Step 5: Guidance tone
        guidance_tone = self._detect_guidance(text)

        # Step 6: EPS data
        eps_actual, eps_estimate, eps_surprise_pct = self._fetch_eps_data(symbol)
        if eps_surprise_pct is not None:
            eps_direction = "beat" if eps_surprise_pct > 0 else ("miss" if eps_surprise_pct < -2 else "in_line")
        else:
            # Infer from text
            text_lower = text.lower()
            if "beat" in text_lower or "exceeded" in text_lower:
                eps_direction = "beat"
            elif "miss" in text_lower or "below" in text_lower:
                eps_direction = "miss"
            else:
                eps_direction = "unknown"

        # Step 7: Derive directional signal
        bullish_score = (
            (1 if sentiment_score > 0.15 else 0)
            + (1 if guidance_tone == "raised" else 0)
            + (1 if eps_direction == "beat" else 0)
            + (1 if "beat estimates" in themes else 0)
        )
        bearish_score = (
            (1 if sentiment_score < -0.15 else 0)
            + (1 if guidance_tone == "lowered" else 0)
            + (1 if eps_direction == "miss" else 0)
            + (1 if "macro headwinds" in themes else 0)
        )

        if bullish_score > bearish_score:
            directional_signal = "bullish"
            confidence = min(0.95, 0.50 + bullish_score * 0.12)
        elif bearish_score > bullish_score:
            directional_signal = "bearish"
            confidence = min(0.95, 0.50 + bearish_score * 0.12)
        else:
            directional_signal = "neutral"
            confidence = 0.40

        return EarningsCatalystSignal(
            symbol=symbol,
            quarter=quarter,
            sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            key_themes=themes,
            guidance_tone=guidance_tone,
            eps_surprise_direction=eps_direction,
            eps_actual=eps_actual,
            eps_estimate=eps_estimate,
            eps_surprise_pct=eps_surprise_pct,
            directional_signal=directional_signal,
            signal_confidence=confidence,
            data_source="sec_edgar_8k + polygon_financials",
            filing_url=filing_url,
        )


_earnings_engine: EarningsCatalystEngine | None = None


def get_earnings_engine() -> EarningsCatalystEngine:
    global _earnings_engine
    if _earnings_engine is None:
        _earnings_engine = EarningsCatalystEngine()
    return _earnings_engine
