from __future__ import annotations

from algochains_mcp import agent_memory


def test_large_nested_memory_value_truncates_without_invalid_json(monkeypatch):
    memory = {
        "latest_session": {
            "summary": "x" * 3000,
            "authority": "agent_memory",
        }
    }
    monkeypatch.setattr(agent_memory, "_read_json", lambda *_args, **_kwargs: memory)

    result = agent_memory.get_openclaw_memory(limit=5)

    value = result["memory"]["latest_session"]
    assert isinstance(value, str)
    assert value.endswith("...")
    assert len(value) == 2000
