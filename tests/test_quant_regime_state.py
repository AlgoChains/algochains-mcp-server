from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone


def _decode(result):
    text = result[0].text if hasattr(result[0], "text") else str(result[0])
    return json.loads(text)


def _call(payload=None):
    import algochains_mcp.server as srv

    return _decode(asyncio.run(srv.call_tool("get_quant_regime_state", payload or {})))


def _write_snapshot(root, *, generated_at: str, stale_after_sec: int = 360):
    state = root / "state"
    state.mkdir()
    (state / "quant_shadow_snapshot.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "stale_after_sec": stale_after_sec,
                "bots": {
                    "mnq": {
                        "symbol": "MNQ",
                        "garch_vol_status": "ok",
                        "ofi_status": "unavailable",
                        "kalman_status": "ok",
                        "hmm_regime_status": "unavailable",
                    }
                },
            }
        )
    )


def _disable_supabase(monkeypatch):
    import algochains_mcp.marketplace.supabase_tools as sb_tools

    monkeypatch.setattr(sb_tools, "_get_sb_client", lambda use_service_role=False: None)


def test_missing_snapshot_degrades_explicitly(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    _disable_supabase(monkeypatch)

    data = _call()

    assert data["snapshot_status"] == "missing"
    assert data["snapshot_error"] == "state/quant_shadow_snapshot.json not found"
    assert data["bot_metrics_live_status"] == "unavailable"
    assert data["agreement_summary_7d_status"] == "unavailable"
    assert data["computes_models"] is False


def test_snapshot_filter_and_ok_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    _disable_supabase(monkeypatch)
    _write_snapshot(tmp_path, generated_at=datetime.now(timezone.utc).isoformat())

    data = _call({"bot_id": "mnq"})

    assert data["snapshot_status"] == "ok"
    assert list(data["bots"].keys()) == ["mnq"]


def test_stale_snapshot_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    _disable_supabase(monkeypatch)
    old = datetime.now(timezone.utc) - timedelta(minutes=20)
    _write_snapshot(tmp_path, generated_at=old.isoformat(), stale_after_sec=60)

    data = _call({"bot_id": "mnq"})

    assert data["snapshot_status"] == "stale"


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name: str, *, view_error: Exception | None = None, metrics_error: Exception | None = None):
        self.table_name = table_name
        self.view_error = view_error
        self.metrics_error = metrics_error

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.table_name == "bot_metrics_live":
            if self.metrics_error:
                raise self.metrics_error
            return _FakeResult([{"bot_id": "mnq", "symbol": "MNQ", "garch_vol_status": "ok"}])
        if self.view_error:
            raise self.view_error
        return _FakeResult([{"bot_id": "mnq", "model_name": "kalman", "agreement_rate_7d": 0.5}])


class _FakeClient:
    def __init__(self, *, view_error: Exception | None = None, metrics_error: Exception | None = None):
        self.view_error = view_error
        self.metrics_error = metrics_error

    def table(self, table_name: str):
        return _FakeQuery(table_name, view_error=self.view_error, metrics_error=self.metrics_error)


def test_summary_permission_denied_is_statused(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    _write_snapshot(tmp_path, generated_at=datetime.now(timezone.utc).isoformat())

    import algochains_mcp.marketplace.supabase_tools as sb_tools

    def fake_client(use_service_role=False):
        if use_service_role:
            return _FakeClient(view_error=Exception("permission denied for view v_quant_model_shadow_summary"))
        return _FakeClient()

    monkeypatch.setattr(sb_tools, "_get_sb_client", fake_client)

    data = _call({"bot_id": "mnq"})

    assert data["bot_metrics_live_status"] == "ok"
    assert data["agreement_summary_7d_status"] == "permission_denied"
    assert data["agreement_summary_7d"] == []


def test_metrics_schema_missing_is_statused(tmp_path, monkeypatch):
    monkeypatch.setenv("ALGOCHAINS_CONTROL_TOWER", str(tmp_path))
    _write_snapshot(tmp_path, generated_at=datetime.now(timezone.utc).isoformat())

    import algochains_mcp.marketplace.supabase_tools as sb_tools

    def fake_client(use_service_role=False):
        return _FakeClient(metrics_error=Exception("Could not find the 'garch_vol_forecast' column"))

    monkeypatch.setattr(sb_tools, "_get_sb_client", fake_client)

    data = _call({"bot_id": "mnq"})

    assert data["bot_metrics_live_status"] == "schema_missing"
