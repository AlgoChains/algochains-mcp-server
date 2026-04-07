"""
AlgoChains Live Bot Intelligence Module
========================================
Provides real-time metrics, system heartbeat awareness, and academic
citation data for the 4 live Tradovate futures bots.

Tools exposed:
  - get_live_bot_metrics(bot_id)     → real P&L, WinRate, Sharpe from logs
  - get_system_heartbeat()           → Mac/Desktop liveness + mode (primary/standby)
  - get_bot_card_data(bot_id)        → full bot card payload for algochains.ai
  - get_strategy_academic_citations(bot_id) → SSRN citations + blueprint links
  - list_bot_research_attachments(bot_id)   → backtest JSON, whitepaper, MCPT badge
"""

from .metrics_parser import parse_bot_metrics, BotMetrics, BOT_LOG_PATHS
from .heartbeat import get_system_heartbeat, SystemHeartbeat
from .academic_registry import get_academic_citations, get_bot_card_data, AcademicCitation

__all__ = [
    "parse_bot_metrics",
    "BotMetrics",
    "BOT_LOG_PATHS",
    "get_system_heartbeat",
    "SystemHeartbeat",
    "get_academic_citations",
    "get_bot_card_data",
    "AcademicCitation",
]
