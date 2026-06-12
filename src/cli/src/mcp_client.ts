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

function normalizeBridgeUrls(bridgeUrl: string): { rootUrl: string; toolUrl: string } {
  const normalized = bridgeUrl.replace(/\/+$/, "");
  if (normalized.endsWith("/api/mcp")) {
    return {
      rootUrl: normalized.slice(0, -"/api/mcp".length),
      toolUrl: normalized,
    };
  }
  if (normalized.endsWith("/tool")) {
    return {
      rootUrl: normalized.slice(0, -"/tool".length),
      toolUrl: normalized,
    };
  }
  return {
    rootUrl: normalized,
    toolUrl: `${normalized}/api/mcp`,
  };
}

export function createMcpClient(bridgeUrl: string, timeoutMs = 30_000): McpClient {
  const urls = normalizeBridgeUrls(bridgeUrl);
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
      const res = await fetchWithTimeout(urls.toolUrl, {
        method: "POST",
        headers,
        body: JSON.stringify({ tool: name, arguments: args }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        return { content: [{ type: "text", text: `Error ${res.status}: ${text}` }], isError: true };
      }
      return res.json() as Promise<McpToolResult>;
    },

    async listTools(): Promise<Array<{ name: string; description: string }>> {
      const res = await fetchWithTimeout(`${urls.rootUrl}/tools`, { headers });
      if (!res.ok) return [];
      const data = await res.json() as { tools?: Array<{ name: string; description: string }> };
      return data.tools ?? [];
    },

    async isHealthy(): Promise<boolean> {
      try {
        const res = await fetchWithTimeout(`${urls.rootUrl}/health`, { headers });
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
