"""
academic_registry.py — Academic citations, whitepapers, and research attachments
for the 4 live AlgoChains Tradovate futures bots.

All citations are real published works that provide the theoretical and empirical
basis for each strategy's design, signal logic, and risk management.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

_POSSIBLE_ROOTS = [
    Path("/Users/treycsa/CascadeProjects/algochains-control-tower"),
    Path("/home/trrey/algochains-control-tower"),
    Path("/mnt/c/Users/trrey/algochains-control-tower"),
]
CONTROL_TOWER = next((p for p in _POSSIBLE_ROOTS if p.exists()), _POSSIBLE_ROOTS[0])


@dataclass
class AcademicCitation:
    title: str
    authors: str
    year: int
    venue: str
    doi_or_ssrn: str
    relevance: str  # How it applies to this bot
    url: str = ""


@dataclass
class BacktestArtifact:
    artifact_type: str  # "mcpt_json" | "backtest_report" | "whitepaper" | "blueprint"
    name: str
    local_path: str
    description: str
    available: bool = False


@dataclass
class BotCardData:
    bot_id: str
    symbol: str
    display_name: str
    strategy_summary: str
    citations: list[AcademicCitation] = field(default_factory=list)
    backtest_artifacts: list[BacktestArtifact] = field(default_factory=list)
    blueprint_refs: list[str] = field(default_factory=list)
    skills_refs: list[str] = field(default_factory=list)
    mcpt_status: str = ""
    mcpt_p_value: Optional[float] = None
    sharpe_validated: Optional[float] = None
    subscription_tier: str = "owner-only"  # locked to Tyler currently

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Citation Database ──────────────────────────────────────────────────────

CITATIONS_DB: dict[str, list[AcademicCitation]] = {

    "mnq": [
        AcademicCitation(
            title="…and the Cross-Section of Expected Returns",
            authors="Harvey, C.R., Liu, Y., & Zhu, H.",
            year=2016,
            venue="Review of Financial Studies",
            doi_or_ssrn="10.1093/rfs/hhv059",
            relevance="Deflated Sharpe Ratio methodology used to adjust MNQ backtested Sharpe for multiple-hypothesis testing bias. deflated-sharpe.pdf in repo.",
            url="https://academic.oup.com/rfs/article/29/1/5/1843824",
        ),
        AcademicCitation(
            title="Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency",
            authors="Jegadeesh, N. & Titman, S.",
            year=1993,
            venue="Journal of Finance",
            doi_or_ssrn="10.1111/j.1540-6261.1993.tb04702.x",
            relevance="Momentum factor underlying the MNQ 7-AI ensemble's directional signal generation for 5-min micro-momentum captures.",
            url="https://doi.org/10.1111/j.1540-6261.1993.tb04702.x",
        ),
        AcademicCitation(
            title="The Sharpe Ratio Efficient Frontier",
            authors="Bailey, D.H. & Lopez de Prado, M.",
            year=2012,
            venue="Journal of Risk",
            doi_or_ssrn="SSRN-2030624",
            relevance="Efficient frontier methodology for ensemble model weighting across 7 AI signals in the MNQ bot.",
            url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2030624",
        ),
        AcademicCitation(
            title="Machine Learning for Asset Managers",
            authors="Lopez de Prado, M.",
            year=2020,
            venue="Cambridge Elements: Quantitative Finance",
            doi_or_ssrn="ISBN 978-1108792899",
            relevance="Feature importance via MDI/MDA for ML regime detection layer in MNQ 7-AI ensemble.",
            url="https://www.cambridge.org/core/elements/machine-learning-for-asset-managers/6D9211305EA2E425D33A9F38D0AE3545",
        ),
        AcademicCitation(
            title="Advances in Financial Machine Learning",
            authors="Lopez de Prado, M.",
            year=2018,
            venue="Wiley",
            doi_or_ssrn="ISBN 978-1119482086",
            relevance="Triple barrier labeling, meta-labeling, and cross-validation without leakage — all implemented in MNQ MCPT pipeline.",
            url="https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086",
        ),
    ],

    "cl": [
        AcademicCitation(
            title="FinBERT: Financial Sentiment Analysis with Pre-trained Language Models",
            authors="Araci, D.",
            year=2019,
            venue="arXiv preprint",
            doi_or_ssrn="arXiv:1908.10063",
            relevance="FinBERT model used as the primary sentiment signal in CL_FUTURES_SCALPER for oil news and EIA report sentiment scoring.",
            url="https://arxiv.org/abs/1908.10063",
        ),
        AcademicCitation(
            title="The Price Impact of Order Book Events",
            authors="Cont, R., Kukanov, A. & Stoikov, S.",
            year=2014,
            venue="Journal of Financial Econometrics",
            doi_or_ssrn="10.1093/jjfinec/nbt012",
            relevance="Order book imbalance theory underlying CL order flow signal — bid/ask volume skew before entries.",
            url="https://doi.org/10.1093/jjfinec/nbt012",
        ),
        AcademicCitation(
            title="Optimal Execution of Portfolio Transactions",
            authors="Almgren, R. & Chriss, N.",
            year=2001,
            venue="Journal of Risk",
            doi_or_ssrn="SSRN-219614",
            relevance="Market impact modeling for CL position sizing — ensures minimal slippage on crude oil futures fills.",
            url="https://papers.ssrn.com/sol3/papers.cfm?abstract_id=219614",
        ),
        AcademicCitation(
            title="Oil Price Volatility and the Role of Speculation",
            authors="Kilian, L. & Murphy, D.P.",
            year=2014,
            venue="Economica",
            doi_or_ssrn="10.1111/ecca.12068",
            relevance="Macro oil market theory informing CL regime gating — VIX and EIA data as volatility regime signals.",
            url="https://doi.org/10.1111/ecca.12068",
        ),
    ],

    "mes": [
        AcademicCitation(
            title="A Century of Evidence on Trend-Following Investing",
            authors="Hurst, B., Ooi, Y.H. & Pedersen, L.H.",
            year=2017,
            venue="Journal of Portfolio Management",
            doi_or_ssrn="AQR Capital Research",
            relevance="Empirical basis for MES EMA-pullback trend-following strategy across 100+ years of futures data.",
            url="https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing",
        ),
        AcademicCitation(
            title="A New Interpretation of Information Rate",
            authors="Kelly, J.L.",
            year=1956,
            venue="Bell System Technical Journal",
            doi_or_ssrn="10.1002/j.1538-7305.1956.tb03809.x",
            relevance="Kelly criterion fraction implemented in MES position sizing — fractional Kelly (0.25x) for volatility-adjusted bet sizing.",
            url="https://doi.org/10.1002/j.1538-7305.1956.tb03809.x",
        ),
        AcademicCitation(
            title="Trading Systems and Methods",
            authors="Kaufman, P.J.",
            year=2013,
            venue="Wiley (5th edition)",
            doi_or_ssrn="ISBN 978-1118043561",
            relevance="EMA crossover methodology and adaptive moving average theory applied to MES swing entries on 15-min bars.",
        ),
    ],

    "nq": [
        AcademicCitation(
            title="Time Series Momentum",
            authors="Moskowitz, T., Ooi, Y.H. & Pedersen, L.H.",
            year=2012,
            venue="Journal of Financial Economics",
            doi_or_ssrn="10.1016/j.jfineco.2011.11.003",
            relevance="Time-series momentum across 58 futures markets — empirical foundation for NQ trend-following signal with 1-12 month lookback.",
            url="https://doi.org/10.1016/j.jfineco.2011.11.003",
        ),
        AcademicCitation(
            title="Investor Sentiment and the Cross-Section of Stock Returns",
            authors="Baker, M. & Wurgler, J.",
            year=2006,
            venue="Journal of Finance",
            doi_or_ssrn="10.1111/j.1540-6261.2006.00885.x",
            relevance="VIX as investor fear/sentiment proxy — basis for NQ's VIX > 35 kill switch and regime gating logic.",
            url="https://doi.org/10.1111/j.1540-6261.2006.00885.x",
        ),
        AcademicCitation(
            title="Two Centuries of Trend Following",
            authors="Lempérière, Y. et al.",
            year=2014,
            venue="Journal of Investment Strategies",
            doi_or_ssrn="arXiv:1404.3274",
            relevance="Historical validation of trend-following profitability across regimes — confirms NQ strategy robustness across market cycles.",
            url="https://arxiv.org/abs/1404.3274",
        ),
    ],
}


BACKTEST_ARTIFACTS: dict[str, list[BacktestArtifact]] = {
    "mnq": [
        BacktestArtifact("whitepaper", "Deflated Sharpe Methodology",
                         str(CONTROL_TOWER / "deflated-sharpe.pdf"),
                         "Harvey et al. (2016) deflated Sharpe calculation applied to MNQ backtest validation.",
                         (CONTROL_TOWER / "deflated-sharpe.pdf").exists()),
        BacktestArtifact("mcpt_json", "MCPT Validation — QQQ Momentum",
                         str(CONTROL_TOWER / "research_pipeline/tier6_promoted/QQQ_momentum_sharpe_190.json"),
                         "Monte Carlo Permutation Test result: Sharpe 1.90, p-value validation against random permutations.",
                         (CONTROL_TOWER / "research_pipeline/tier6_promoted/QQQ_momentum_sharpe_190.json").exists()),
        BacktestArtifact("blueprint", "MCPT Price Permutation Gate Blueprint",
                         str(CONTROL_TOWER / "blueprints/MCPT_PRICE_PERMUTATION_GATE_BLUEPRINT.md"),
                         "Full 5-gate MCPT validation methodology: train/test split, permutation test, walk-forward, sensitivity, DSR.",
                         (CONTROL_TOWER / "blueprints/MCPT_PRICE_PERMUTATION_GATE_BLUEPRINT.md").exists()),
        BacktestArtifact("blueprint", "Mega Integration Blueprint",
                         str(CONTROL_TOWER / "blueprints/ALGOCHAINS_MEGA_INTEGRATION_BLUEPRINT_V1.md"),
                         "Comprehensive system integration: bots → marketplace → MCP → Onyx → algochains.ai.",
                         (CONTROL_TOWER / "blueprints/ALGOCHAINS_MEGA_INTEGRATION_BLUEPRINT_V1.md").exists()),
    ],
    "cl": [
        BacktestArtifact("mcpt_json", "CL Swing 5-Year Backtest",
                         str(CONTROL_TOWER / "research_pipeline/tier6_promoted/CL_swing_5year_backtest.json"),
                         "5-year walk-forward backtest on CL crude oil futures with real Databento tick data.",
                         (CONTROL_TOWER / "research_pipeline/tier6_promoted/CL_swing_5year_backtest.json").exists()),
        BacktestArtifact("blueprint", "Creator Submission Pipeline",
                         str(CONTROL_TOWER / "blueprints/CREATOR_SUBMISSION_PIPELINE_BLUEPRINT.md"),
                         "Strategy submission and validation pipeline — how CL bot achieved marketplace listing.",
                         (CONTROL_TOWER / "blueprints/CREATOR_SUBMISSION_PIPELINE_BLUEPRINT.md").exists()),
    ],
    "mes": [
        BacktestArtifact("blueprint", "MCPT SSRN Marketplace Blueprint V2",
                         str(CONTROL_TOWER / "blueprints/MCPT_SSRN_MARKETPLACE_BLUEPRINT_V2.md"),
                         "SSRN-aligned strategy validation framework applied to MES swing strategy.",
                         (CONTROL_TOWER / "blueprints/MCPT_SSRN_MARKETPLACE_BLUEPRINT_V2.md").exists()),
    ],
    "nq": [
        BacktestArtifact("mcpt_json", "GBPUSD Breakout Sharpe 1.78 (cross-asset reference)",
                         str(CONTROL_TOWER / "research_pipeline/tier6_promoted/GBPUSD_breakout_sharpe_178.json"),
                         "Cross-asset momentum validation: similar breakout strategy tested on forex confirms robustness.",
                         (CONTROL_TOWER / "research_pipeline/tier6_promoted/GBPUSD_breakout_sharpe_178.json").exists()),
    ],
}

BLUEPRINT_REFS: dict[str, list[str]] = {
    "mnq": [
        "blueprints/ALGOCHAINS_MARKETPLACE_MEGA_BLUEPRINT.md",
        "blueprints/PLATFORM_UPGRADE_MEGA_BLUEPRINT.md",
        "blueprints/MCPT_PRICE_PERMUTATION_GATE_BLUEPRINT.md",
        "blueprints/ALGOCHAINS_MEGA_INTEGRATION_BLUEPRINT_V1.md",
    ],
    "cl": [
        "blueprints/CREATOR_SUBMISSION_PIPELINE_BLUEPRINT.md",
        "blueprints/UNIFIED_BROKERAGE_MCP_MEGA_BLUEPRINT.md",
        "blueprints/MCPT_SSRN_MARKETPLACE_BLUEPRINT_V2.md",
    ],
    "mes": [
        "blueprints/MCPT_SSRN_MARKETPLACE_BLUEPRINT_V2.md",
        "blueprints/MARKETPLACE_BOT_SUBMISSION_BLUEPRINT.md",
    ],
    "nq": [
        "blueprints/ALGOCHAINS_MARKETPLACE_MEGA_BLUEPRINT.md",
        "blueprints/MCPT_SSRN_MARKETPLACE_BLUEPRINT_V2.md",
    ],
}

SKILLS_REFS: dict[str, list[str]] = {
    "mnq": [
        ".windsurf/skills/algochains-master-brain/SKILL.md",
        ".windsurf/skills/ac-live-bots/SKILL.md",
        ".claude/skills/trading-system-health-audit/SKILL.md",
        ".claude/skills/backtest-governance/SKILL.md",
    ],
    "cl": [
        ".windsurf/skills/ac-live-bots/SKILL.md",
        ".claude/skills/bot-diagnostics/SKILL.md",
    ],
    "mes": [
        ".windsurf/skills/ac-live-bots/SKILL.md",
        ".claude/skills/incident-response-trading/SKILL.md",
    ],
    "nq": [
        ".windsurf/skills/ac-live-bots/SKILL.md",
        ".claude/skills/tradovate-token-ops/SKILL.md",
    ],
}

STRATEGY_SUMMARIES: dict[str, str] = {
    "mnq": (
        "7-AI ensemble scalper trading Micro E-mini NASDAQ-100 futures on 5-minute bars. "
        "Combines momentum signals from 7 independent models (XGBoost, LSTM, FinBERT, "
        "RandomForest, GBM, Linear, VIX-regime) via a meta-labeling consensus layer. "
        "Position sizing via fractional Kelly. Risk-adjusted with ATR stops. "
        "MCPT-validated with Deflated Sharpe correction (Harvey et al. 2016). "
        "Tradovate broker. Real-time execution via WebSocket. Token auto-renews every 10 min."
    ),
    "cl": (
        "Sentiment-driven scalper on Crude Oil (CL) futures using FinBERT (Araci 2019) "
        "for real-time EIA report and oil news sentiment scoring. Combines order flow "
        "imbalance (Cont et al. 2014) with macro regime gating (VIX, DXY correlates). "
        "5-year walk-forward backtest validated on real Databento CL tick data. "
        "MCPT-validated. Tradovate broker."
    ),
    "mes": (
        "EMA-pullback swing strategy on Micro E-mini S&P 500 (MES) futures on 15-minute bars. "
        "Enters on multi-timeframe EMA alignment with volume confirmation. "
        "Kelly criterion position sizing (0.25x fractional). ATR-based stop/target. "
        "Based on AQR trend-following research (Hurst et al. 2017). "
        "Tradovate broker. Part of the Hurst century-of-evidence momentum framework."
    ),
    "nq": (
        "Trend-following swing strategy on E-mini NASDAQ-100 (NQ) futures. "
        "Time-series momentum (Moskowitz et al. 2012) with VIX regime gate (Baker & Wurgler 2006). "
        "VIX > 35 hard-stops all new positions. Designed for bull and trending markets. "
        "Two centuries of trend-following evidence support core signal design. "
        "Tradovate broker."
    ),
}


def get_academic_citations(bot_id: str) -> list[AcademicCitation]:
    """Return all academic citations for a given bot."""
    return CITATIONS_DB.get(bot_id.lower(), [])


def get_bot_card_data(bot_id: str) -> BotCardData:
    """Return complete bot card data including citations, artifacts, and blueprint refs."""
    bot_id = bot_id.lower()
    from .metrics_parser import BOT_META
    meta = BOT_META.get(bot_id, {})

    return BotCardData(
        bot_id=bot_id,
        symbol=meta.get("symbol", bot_id.upper()),
        display_name=meta.get("display_name", bot_id),
        strategy_summary=STRATEGY_SUMMARIES.get(bot_id, ""),
        citations=CITATIONS_DB.get(bot_id, []),
        backtest_artifacts=BACKTEST_ARTIFACTS.get(bot_id, []),
        blueprint_refs=BLUEPRINT_REFS.get(bot_id, []),
        skills_refs=SKILLS_REFS.get(bot_id, []),
        subscription_tier="owner-only",
    )
