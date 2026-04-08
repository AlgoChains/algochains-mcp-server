"""
AlgoChains Live Bot Intelligence Module
========================================
Provides real-time metrics, system heartbeat awareness, academic citations,
and operational management (restart/flatten) for the 4 live Tradovate bots.

Read-only tools:
  - get_live_bot_metrics(bot_id)            → real P&L, WinRate, Sharpe from logs
  - get_system_heartbeat()                   → Mac/Desktop liveness + mode
  - get_bot_card_data(bot_id)               → full bot card payload for algochains.ai
  - get_strategy_academic_citations(bot_id) → SSRN citations + blueprint links
  - list_bot_research_attachments(bot_id)   → backtest JSON, whitepaper, MCPT badge
  - get_position_state(bot_id)              → persisted position state (flat/qty/entry)
  - get_bracket_status(bot_id)              → bracket mode (live/oso_only/none/unknown)
  - get_ai_pipeline_health(bot_id)          → AI ensemble status, quota errors, shadow mode
  - get_all_bot_ops_status()                → full ops snapshot for all 4 bots

Owner-gated tools (require OWNER_API_TOKEN):
  - restart_bot(bot_id, owner_token)        → kill + restart a bot process
  - flatten_position_tradovate(symbol, owner_token) → close all contracts via Tradovate MKT
"""

from .metrics_parser import parse_bot_metrics, BotMetrics, BOT_LOG_PATHS
from .heartbeat import get_system_heartbeat, SystemHeartbeat
from .academic_registry import get_academic_citations, get_bot_card_data, AcademicCitation
from .bot_ops import (
    get_position_state,
    get_bracket_status,
    get_ai_pipeline_health,
    get_all_bot_ops_status,
    restart_bot,
    flatten_position_tradovate,
)

__all__ = [
    "parse_bot_metrics",
    "BotMetrics",
    "BOT_LOG_PATHS",
    "get_system_heartbeat",
    "SystemHeartbeat",
    "get_academic_citations",
    "get_bot_card_data",
    "AcademicCitation",
    "get_position_state",
    "get_bracket_status",
    "get_ai_pipeline_health",
    "get_all_bot_ops_status",
    "restart_bot",
    "flatten_position_tradovate",
]
