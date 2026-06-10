import { afterEach, expect, test } from "bun:test";
import { createMcpClient } from "./mcp_client";

interface CapturedRequest {
  url: string;
  init?: RequestInit;
}

const originalFetch = globalThis.fetch;
const originalSubKey = process.env.ALGOCHAINS_SUB_KEY;

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalSubKey === undefined) {
    delete process.env.ALGOCHAINS_SUB_KEY;
  } else {
    process.env.ALGOCHAINS_SUB_KEY = originalSubKey;
  }
});

function installFetchMock(responseBody: unknown): CapturedRequest[] {
  const calls: CapturedRequest[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  return calls;
}

test("callTool posts to /api/mcp for bridge host roots", async () => {
  const calls = installFetchMock({ content: [{ type: "text", text: "ok" }] });
  const client = createMcpClient("http://127.0.0.1:8090/");

  await client.callTool("get_my_portfolio", { foo: "bar" });

  expect(calls).toHaveLength(1);
  expect(calls[0].url).toBe("http://127.0.0.1:8090/api/mcp");
  expect(calls[0].init?.method).toBe("POST");
  expect(calls[0].init?.body).toBe(JSON.stringify({
    tool: "get_my_portfolio",
    arguments: { foo: "bar" },
  }));
});

test("callTool preserves explicit /api/mcp endpoint configs", async () => {
  const calls = installFetchMock({ content: [{ type: "text", text: "ok" }] });
  const client = createMcpClient("https://mcp.algochains.ai/api/mcp");

  await client.callTool("get_my_paper_positions", {});

  expect(calls).toHaveLength(1);
  expect(calls[0].url).toBe("https://mcp.algochains.ai/api/mcp");
});

test("tool discovery and health use the bridge root with endpoint configs", async () => {
  const calls = installFetchMock({ tools: [{ name: "detect_market_regime", description: "Regime" }] });
  const client = createMcpClient("https://mcp.algochains.ai/api/mcp");

  const tools = await client.listTools();
  await client.isHealthy();

  expect(tools).toEqual([{ name: "detect_market_regime", description: "Regime" }]);
  expect(calls.map(call => call.url)).toEqual([
    "https://mcp.algochains.ai/tools",
    "https://mcp.algochains.ai/health",
  ]);
});

test("subscriber keys are sent to the normalized bridge endpoint", async () => {
  process.env.ALGOCHAINS_SUB_KEY = "sub_live_test_secret";
  const calls = installFetchMock({ content: [{ type: "text", text: "ok" }] });
  const client = createMcpClient("https://mcp.algochains.ai");

  await client.callTool("place_paper_order", { symbol: "MNQ" });

  const headers = new Headers(calls[0].init?.headers as HeadersInit);
  expect(calls[0].url).toBe("https://mcp.algochains.ai/api/mcp");
  expect(headers.get("X-Api-Key")).toBe("sub_live_test_secret");
});
