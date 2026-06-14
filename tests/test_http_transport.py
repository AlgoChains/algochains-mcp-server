import json
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent

from algochains_mcp.http_transport import _dispatch_jsonrpc


@pytest.mark.asyncio
async def test_tools_call_uses_guarded_call_tool():
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "mcp_tool_manifest", "arguments": {}},
    }

    with patch(
        "algochains_mcp.server.call_tool",
        new=AsyncMock(return_value=[TextContent(type="text", text=json.dumps({"ok": True}))]),
    ) as mock_call:
        result = await _dispatch_jsonrpc(object(), body, "session-1")

    mock_call.assert_awaited_once_with("mcp_tool_manifest", {})
    assert "error" not in result
    assert result["result"]["content"][0]["text"] == '{"ok": true}'
