/**
 * @algochains/sdk — Runtime client factory
 * Wraps MCPorter's createRuntime + createServerProxy into a typed AlgoChainsClient.
 *
 * Usage:
 *   const { createAlgoChainsClient } = require("@algochains/sdk");
 *   const ac = await createAlgoChainsClient();
 *   const regime = await ac.detectMarketRegime();
 */
"use strict";

async function createAlgoChainsClient(options = {}) {
  const { createRuntime, createServerProxy } = await import("mcporter");
  const rt = options.runtime || (await createRuntime());
  const serverName = options.serverName || "algochains";
  const proxy = createServerProxy(rt, serverName);

  const snakeToCamel = (s) => s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());

  const client = new Proxy(
    {},
    {
      get(_, prop) {
        if (prop === "close") return () => rt.close();
        if (prop === "call") return (tool, params) => proxy.call(tool, params || {});
        if (typeof prop === "string") {
          const toolName = prop.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
          return (params) => proxy.call(toolName, params || {});
        }
      },
    }
  );

  return client;
}

/**
 * createBridgeClient — HTTP bridge client for programmatic access.
 *
 * Sends MCP tool calls directly to the AlgoChains hosted bridge without
 * the stdio transport. Useful for CI/CD, backend services, and scripts.
 *
 * @param {object} options
 * @param {string} [options.apiKey]    Developer key (ac_live_* or AC_DEV_KEY env)
 * @param {string} [options.baseUrl]   Bridge URL (default: https://mcp.algochains.ai)
 * @param {number} [options.timeoutMs] Request timeout ms (default: 30000)
 */
function createBridgeClient(options = {}) {
  const apiKey = options.apiKey || process.env.AC_DEV_KEY || "";
  const baseUrl = (options.baseUrl || process.env.ALGOCHAINS_BRIDGE_URL || "https://mcp.algochains.ai").replace(/\/$/, "");
  const timeoutMs = options.timeoutMs || 30_000;

  if (!apiKey) {
    throw new Error(
      "createBridgeClient: no apiKey provided and AC_DEV_KEY env var is not set. " +
      "Create a key with: algochains keys create"
    );
  }

  async function call(toolName, params = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(`${baseUrl}/api/mcp`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Api-Key": apiKey,
        },
        body: JSON.stringify({ tool: toolName, arguments: params }),
        signal: controller.signal,
      });
      const data = await resp.json().catch(() => ({}));
      return { ok: resp.ok, status: resp.status, data, error: resp.ok ? undefined : (data.error || `HTTP ${resp.status}`) };
    } catch (err) {
      return { ok: false, status: 0, data: null, error: err.name === "AbortError" ? `Timeout after ${timeoutMs}ms` : String(err) };
    } finally {
      clearTimeout(timeout);
    }
  }

  async function health() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(`${baseUrl}/health`, {
        headers: { "X-Api-Key": apiKey },
        signal: controller.signal,
      });
      const data = await resp.json().catch(() => ({}));
      return { ok: resp.ok, status: resp.status, data, error: resp.ok ? undefined : `HTTP ${resp.status}` };
    } catch (err) {
      return { ok: false, status: 0, data: null, error: String(err) };
    } finally {
      clearTimeout(timeout);
    }
  }

  return { call, health };
}

module.exports = { createAlgoChainsClient, createBridgeClient };
