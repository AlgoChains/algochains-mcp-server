"""Regression tests for fallback GPU routing."""

import asyncio

from algochains_mcp.ml_engine import gpu_dispatcher


def test_fallback_vendor_analysis_stays_off_sonia_air(monkeypatch):
    monkeypatch.setattr(gpu_dispatcher, "route_compute", None)
    monkeypatch.setattr(gpu_dispatcher.sys, "platform", "linux")
    dispatcher = gpu_dispatcher.GPUDispatcher()

    result = asyncio.run(dispatcher.dispatch("kalshi_backtest_analysis", {}, prefer_gpu="auto"))

    assert result["status"] == "ok"
    assert result["target"] == "desktop"


def test_fallback_explicit_event_polling_uses_sonia_air(monkeypatch):
    monkeypatch.setattr(gpu_dispatcher, "route_compute", None)
    dispatcher = gpu_dispatcher.GPUDispatcher()

    result = asyncio.run(dispatcher.dispatch("kalshi_event_polling", {}, prefer_gpu="auto"))

    assert result["status"] == "ok"
    assert result["target"] == "sonia_air"
