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

module.exports = { createAlgoChainsClient };
