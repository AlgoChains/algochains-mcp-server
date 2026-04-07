"""
AlgoChains Live Bot Dashboard — FastAPI + SSE
=============================================
Real-time streaming dashboard for all 4 live futures bots.
Serves live P&L, signals, positions, and bot state via:
  - Server-Sent Events (SSE) for browser streaming
  - REST endpoints for snapshot queries
  - MCP resource subscriptions for agent sessions

Architecture:
  FastAPI app on port 8766 (separate from MCP stdio on 8765)
  SSE stream: GET /stream/{bot_name} → live event feed
  Snapshot:   GET /dashboard         → current state JSON
  Positions:  GET /positions         → open positions
  History:    GET /trades/{bot}      → trade history

Run:
  python -m algochains_mcp.dashboard.live_dashboard
  uvicorn algochains_mcp.dashboard.live_dashboard:app --port 8766 --reload
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import AsyncIterator

try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

CONTROL_TOWER = Path(os.path.expanduser("~/CascadeProjects/algochains-control-tower"))
METRICS_DB = Path.home() / ".algochains" / "bot_metrics.db"


def _load_daemon():
    """Dynamically load the metrics daemon from control tower."""
    daemon_path = CONTROL_TOWER / "autonomous" / "bot_metrics_streaming.py"
    if not daemon_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("bot_metrics_streaming", str(daemon_path))
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod.MetricsStreamingDaemon()


_daemon = None


def get_daemon():
    global _daemon
    if _daemon is None:
        _daemon = _load_daemon()
    return _daemon


if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="AlgoChains Live Bot Dashboard",
        description="Real-time dashboard for 4 live futures bots (MNQ, CL, MES, NQ)",
        version="21.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AlgoChains — Live Bot Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0a0a0f;
      color: #e0e0f0;
      font-family: 'SF Mono', 'Fira Code', monospace;
      padding: 20px;
    }
    header {
      display: flex; align-items: center; justify-content: space-between;
      border-bottom: 1px solid #1e3a5f;
      padding-bottom: 16px; margin-bottom: 24px;
    }
    header h1 { font-size: 1.4rem; color: #4fc3f7; letter-spacing: 1px; }
    .status-dot { width: 10px; height: 10px; border-radius: 50%;
                  background: #4caf50; display: inline-block; margin-right: 8px;
                  box-shadow: 0 0 6px #4caf50; animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
    .account-bar {
      background: #0d1b2a; border-radius: 8px; padding: 12px 20px;
      display: flex; gap: 40px; margin-bottom: 24px;
      border: 1px solid #1e3a5f;
    }
    .account-item { display: flex; flex-direction: column; }
    .account-item label { font-size: 0.7rem; color: #7986cb; text-transform: uppercase; letter-spacing: 1px; }
    .account-item span { font-size: 1.1rem; font-weight: bold; }
    .bot-grid {
      display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px; margin-bottom: 24px;
    }
    .bot-card {
      background: #0d1b2a; border: 1px solid #1e3a5f; border-radius: 10px;
      padding: 16px; transition: border-color .3s;
    }
    .bot-card.active { border-color: #4fc3f7; }
    .bot-card.position { border-color: #ff9800; }
    .bot-card h3 { font-size: 0.9rem; color: #4fc3f7; margin-bottom: 12px; }
    .bot-stat { display: flex; justify-content: space-between; font-size: 0.8rem;
                padding: 4px 0; border-bottom: 1px solid #111; }
    .bot-stat label { color: #7986cb; }
    .positive { color: #4caf50; }
    .negative { color: #f44336; }
    .neutral { color: #9e9e9e; }
    .event-feed {
      background: #0d1b2a; border: 1px solid #1e3a5f; border-radius: 10px;
      padding: 16px; height: 300px; overflow-y: auto;
    }
    .event-feed h3 { font-size: 0.9rem; color: #4fc3f7; margin-bottom: 12px; }
    .event { font-size: 0.75rem; padding: 4px 0; border-bottom: 1px solid #0f1923;
             display: flex; gap: 12px; }
    .event-time { color: #546e7a; min-width: 80px; }
    .event-fill { color: #4caf50; }
    .event-signal { color: #ffc107; }
    .event-rejected { color: #ef5350; }
    .event-position { color: #ff9800; }
    footer { text-align: center; font-size: 0.7rem; color: #546e7a; margin-top: 20px; }
  </style>
</head>
<body>
  <header>
    <h1>⚡ AlgoChains Live Dashboard</h1>
    <div><span class="status-dot"></span><span id="status">Connecting...</span></div>
  </header>

  <div class="account-bar">
    <div class="account-item"><label>Cash Balance</label><span id="cash">—</span></div>
    <div class="account-item"><label>Open Positions</label><span id="open-pos">—</span></div>
    <div class="account-item"><label>Total Trades</label><span id="total-trades">—</span></div>
    <div class="account-item"><label>Last Update</label><span id="last-update">—</span></div>
  </div>

  <div class="bot-grid" id="bot-grid"></div>

  <div class="event-feed">
    <h3>📡 Live Event Feed</h3>
    <div id="events"></div>
  </div>

  <footer>AlgoChains MCP Server v21.0 · Real data only · Private showcase mode</footer>

  <script>
    const BOT_NAMES = ['MNQ_Upgraded_Scalper', 'CL_Swing_Scalper', 'MES_EMA_Swing', 'NQ_EMA_Swing'];
    let lastData = {};

    function fmt(n) { return n == null ? '—' : (typeof n === 'number' ? n.toFixed(2) : n); }
    function fmtPnl(n) {
      if (n == null) return '<span class="neutral">—</span>';
      const cls = n >= 0 ? 'positive' : 'negative';
      return `<span class="${cls}">${n >= 0 ? '+' : ''}$${n.toFixed(2)}</span>`;
    }
    function fmtPct(n) {
      if (n == null) return '—';
      const cls = n >= 0.55 ? 'positive' : n >= 0.45 ? 'neutral' : 'negative';
      return `<span class="${cls}">${(n * 100).toFixed(1)}%</span>`;
    }
    function ts() { return new Date().toLocaleTimeString(); }

    function renderBots(data) {
      const grid = document.getElementById('bot-grid');
      const bots = data.bots || {};
      const positions = (data.open_positions || []).reduce((a,p) => { a[p.bot_name]=p; return a; }, {});
      const sys = data.system || {};

      grid.innerHTML = BOT_NAMES.map(name => {
        const b = bots[name] || {};
        const pos = positions[name];
        const pid = sys.pids?.[name];
        const running = !!pid;
        const cardClass = pos ? 'bot-card position' : (running ? 'bot-card active' : 'bot-card');
        return `<div class="${cardClass}">
          <h3>${name.replace(/_/g,' ')} ${running ? '🟢' : '🔴'}</h3>
          <div class="bot-stat"><label>Symbol</label><span>${b.symbol || name.split('_')[0]}</span></div>
          <div class="bot-stat"><label>Total P&L</label>${fmtPnl(b.total_pnl_usd)}</div>
          <div class="bot-stat"><label>Win Rate</label>${fmtPct(b.win_rate)}</div>
          <div class="bot-stat"><label>Sharpe</label><span>${fmt(b.sharpe)}</span></div>
          <div class="bot-stat"><label>Trades</label><span>${b.total_trades || 0}</span></div>
          <div class="bot-stat"><label>Max DD</label>${fmtPnl(-(b.max_drawdown || 0))}</div>
          ${pos ? `<div class="bot-stat"><label>Live P&L</label>${fmtPnl(pos.current_pnl)}</div>
          <div class="bot-stat"><label>Entry</label><span>$${fmt(pos.entry_price)}</span></div>` : ''}
          <div class="bot-stat"><label>PID</label><span class="${running?'positive':'negative'}">${pid || 'dead'}</span></div>
        </div>`;
      }).join('');

      // Account bar
      const allTrades = Object.values(bots).reduce((s,b) => s + (b.total_trades||0), 0);
      document.getElementById('cash').textContent = `$${fmt(sys.cash_balance || 0)}`;
      document.getElementById('open-pos').textContent = data.open_positions?.length || 0;
      document.getElementById('total-trades').textContent = allTrades;
      document.getElementById('last-update').textContent = ts();
    }

    function addEvent(type, msg) {
      const el = document.getElementById('events');
      const cls = type === 'fill' ? 'event-fill' : type === 'signal' ? 'event-signal'
                  : type === 'rejected' ? 'event-rejected' : 'event-position';
      el.insertAdjacentHTML('afterbegin',
        `<div class="event"><span class="event-time">${ts()}</span><span class="${cls}">${msg}</span></div>`);
      if (el.children.length > 100) el.removeChild(el.lastChild);
    }

    // Initial load
    fetch('/dashboard').then(r => r.json()).then(d => { lastData = d; renderBots(d); });

    // SSE stream
    const es = new EventSource('/stream/all');
    es.addEventListener('dashboard', e => {
      try { const d = JSON.parse(e.data); lastData = d; renderBots(d); } catch(ex) {}
    });
    es.addEventListener('event', e => {
      try {
        const ev = JSON.parse(e.data);
        const type = ev.type === 'exit' ? 'fill' : ev.type === 'signal' && ev.rejected ? 'rejected' : ev.type;
        const msg = ev.type === 'exit' ? `${ev.bot}: EXIT P&L ${ev.pnl>=0?'+':''}$${(ev.pnl||0).toFixed(2)}`
                  : ev.type === 'signal' ? `${ev.bot}: ${ev.direction} ${ev.rejected ? 'REJECTED' : 'SIGNAL'} conf=${((ev.confidence||0)*100).toFixed(0)}%`
                  : ev.type === 'position_update' ? `${ev.bot}: P&L ${(ev.pnl||0)>=0?'+':''}$${(ev.pnl||0).toFixed(2)}`
                  : JSON.stringify(ev);
        addEvent(type, msg);
      } catch(ex) {}
    });
    es.onopen = () => { document.getElementById('status').textContent = 'Connected'; };
    es.onerror = () => { document.getElementById('status').textContent = 'Reconnecting...'; };

    // Refresh dashboard every 30s as fallback
    setInterval(() => fetch('/dashboard').then(r=>r.json()).then(d=>{lastData=d;renderBots(d);}), 30000);
  </script>
</body>
</html>"""

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/dashboard")
    async def dashboard():
        d = get_daemon()
        if not d:
            return JSONResponse({"error": "Bot metrics daemon not available", "hint": "Check control tower path"})
        return JSONResponse(d.get_dashboard())

    @app.get("/positions")
    async def positions():
        d = get_daemon()
        if not d:
            return JSONResponse({"positions": []})
        return JSONResponse({"positions": d.db.get_open_positions()})

    @app.get("/trades/{bot_name}")
    async def trades(bot_name: str, limit: int = 50):
        d = get_daemon()
        if not d:
            return JSONResponse({"trades": []})
        return JSONResponse({
            "bot": bot_name,
            "stats": d.db.get_stats(bot_name),
            "trades": d.db.get_recent_trades(bot_name, limit),
        })

    async def _event_generator(bot_name: str) -> AsyncIterator[str]:
        """SSE generator — ticks the daemon and streams dashboard + events."""
        d = get_daemon()
        if not d:
            yield f"event: error\ndata: {{\"error\": \"daemon unavailable\"}}\n\n"
            return

        # Send initial snapshot
        snapshot = d.get_dashboard()
        yield f"event: dashboard\ndata: {json.dumps(snapshot)}\n\n"

        while True:
            await asyncio.sleep(5)
            try:
                events = d.tick()
                # Send events
                for ev in events:
                    if bot_name == "all" or ev.get("bot", "").lower() in bot_name.lower():
                        yield f"event: event\ndata: {json.dumps(ev)}\n\n"
                # Send dashboard snapshot every tick
                snap = d.get_dashboard()
                yield f"event: dashboard\ndata: {json.dumps(snap)}\n\n"
            except Exception as exc:
                yield f"event: error\ndata: {{\"error\": \"{exc}\"}}\n\n"
                await asyncio.sleep(10)

    @app.get("/stream/{bot_name}")
    async def stream(bot_name: str, request: Request):
        return StreamingResponse(
            _event_generator(bot_name),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/health")
    async def health():
        d = get_daemon()
        return JSONResponse({
            "status": "ok" if d else "degraded",
            "daemon": d is not None,
            "db": str(METRICS_DB) if METRICS_DB.exists() else "not_found",
        })


def run():
    import logging as _logging
    _log = _logging.getLogger("algochains_mcp.dashboard")
    if not FASTAPI_AVAILABLE:
        _log.error("FastAPI not installed — live dashboard unavailable. Run: pip install fastapi uvicorn")
        return
    import uvicorn
    host = __import__("os").getenv("ALGOCHAINS_DASHBOARD_HOST", "127.0.0.1")
    port = int(__import__("os").getenv("ALGOCHAINS_DASHBOARD_PORT", "8766"))
    uvicorn.run("algochains_mcp.dashboard.live_dashboard:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    run()
