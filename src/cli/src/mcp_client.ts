/**
 * AlgoChains CLI — MCP Bridge Client
 * Calls the AlgoChains MCP HTTP bridge at localhost:8090 (or configured URL).
 * Falls back gracefully when the bridge is unavailable.
 */

export interface McpToolResult {
  content: Array<{ type: string; text: string }>;
  isError?: boolean;
}

export interface McpClient {
  callTool(name: string, args: Record<string, unknown>): Promise<McpToolResult>;
  listTools(): Promise<Array<{ name: string; description: string }>>;
  isHealthy(): Promise<boolean>;
}

function bridgePath(bridgeUrl: string, path: string): string {
  return `${bridgeUrl.replace(/\/+$/, "")}${path}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isMcpToolResult(value: unknown): value is McpToolResult {
  if (!isRecord(value) || !Array.isArray(value.content)) return false;
  return value.content.every(
    item =>
      isRecord(item) &&
      typeof item.type === "string" &&
      typeof item.text === "string",
  );
}

function formatBridgePayload(payload: unknown): string {
  if (typeof payload === "string") return payload;
  const formatted = JSON.stringify(payload, null, 2);
  return formatted ?? String(payload);
}

function bridgePayloadIsError(payload: unknown): boolean {
  return isRecord(payload) && payload.error !== undefined;
}

function normalizeToolResult(payload: unknown): McpToolResult {
  if (isMcpToolResult(payload)) return payload;
  return {
    content: [{ type: "text", text: formatBridgePayload(payload) }],
    isError: bridgePayloadIsError(payload),
  };
}

export function createMcpClient(bridgeUrl: string, timeoutMs = 30_000): McpClient {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "User-Agent": "algochains-cli/22.5.0",
  };

  // Subscriber keys (sub_live_*) use X-Api-Key on the HTTP bridge.
  const subKey = process.env.ALGOCHAINS_SUB_KEY;
  if (subKey) {
    headers["X-Api-Key"] = subKey;
  } else {
    const devKey =
      process.env.ALGOCHAINS_BRIDGE_KEY
      ?? process.env.ALGOCHAINS_DEVELOPER_KEY
      ?? process.env.ALGOCHAINS_API_KEY;
    if (devKey) headers["X-Api-Key"] = devKey;
  }

  async function fetchWithTimeout(url: string, init: RequestInit): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...init, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    async callTool(name: string, args: Record<string, unknown>): Promise<McpToolResult> {
      const res = await fetchWithTimeout(bridgePath(bridgeUrl, "/api/mcp"), {
        method: "POST",
        headers,
        body: JSON.stringify({ tool: name, arguments: args }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        return { content: [{ type: "text", text: `Error ${res.status}: ${text}` }], isError: true };
      }
      return normalizeToolResult(await res.json());
    },

    async listTools(): Promise<Array<{ name: string; description: string }>> {
      const res = await fetchWithTimeout(bridgePath(bridgeUrl, "/tools"), { headers });
      if (!res.ok) return [];
      const data = await res.json() as { tools?: Array<{ name: string; description: string }> };
      return data.tools ?? [];
    },

    async isHealthy(): Promise<boolean> {
      try {
        const res = await fetchWithTimeout(bridgePath(bridgeUrl, "/health"), { headers });
        return res.ok;
      } catch { return false; }
    },
  };
}

/** Parse MCP result text content into a plain string */
export function extractText(result: McpToolResult): string {
  return result.content
    .filter(c => c.type === "text")
    .map(c => c.text)
    .join("\n");
}
