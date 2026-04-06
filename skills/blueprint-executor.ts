// blueprint-executor.ts — Read blueprint queue, execute highest-priority item
// Replaces: manual blueprint triage + multi-tool bash composition
// Composes: algochains (discover_tools, backtest, validate) + slack
import { createRuntime, createServerProxy } from "mcporter";

interface Blueprint {
  id: string;
  title: string;
  priority: "P0" | "P1" | "P2" | "P3";
  status: "queued" | "in_progress" | "done" | "blocked";
  tools: string[];
  description: string;
}

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Discover available tools for capability mapping
    const [toolsResult, marketplace] = await Promise.all([
      ac.call("discover_tools", { query: "backtest validate optimize deploy" }),
      ac.call("browse_strategy_marketplace", { min_sharpe: 2.0 }),
    ]);

    const availableTools = toolsResult?.tools ?? toolsResult ?? [];
    const toolNames = Array.isArray(availableTools)
      ? availableTools.map((t: any) => t.name ?? t).filter(Boolean)
      : [];

    // 2. Blueprint queue (in production, read from Pinecone/DB)
    // For now, report capability status
    const capabilities = {
      backtest: toolNames.includes("run_backtest"),
      validate: toolNames.includes("validate_strategy"),
      optimize: toolNames.includes("optimize_strategy"),
      deploy: toolNames.includes("deploy_strategy"),
      marketplace: toolNames.includes("browse_strategy_marketplace"),
      intent: toolNames.includes("execute_intent"),
      regime: toolNames.includes("detect_market_regime"),
      shadow: toolNames.includes("create_shadow_portfolio"),
    };

    const capCount = Object.values(capabilities).filter(Boolean).length;
    const totalCaps = Object.keys(capabilities).length;

    // 3. Check marketplace for strategies needing pipeline work
    const bots = marketplace?.strategies ?? marketplace?.bots ?? [];
    const needsValidation = bots.filter(
      (b: any) => !b.validated || b.status === "pending"
    );

    // 4. Compose status report
    const lines = [
      `📋 *Blueprint Executor Status* — ${new Date().toLocaleDateString()}`,
      ``,
      `*Tool Capabilities:* ${capCount}/${totalCaps} available`,
    ];

    for (const [cap, available] of Object.entries(capabilities)) {
      lines.push(`  ${available ? "✅" : "❌"} ${cap}`);
    }

    lines.push(
      ``,
      `*Marketplace:* ${bots.length} total bots`,
      `*Needs Validation:* ${needsValidation.length} pending`,
    );

    if (needsValidation.length > 0) {
      lines.push(``, `*Next Blueprint Action:* Validate ${Math.min(needsValidation.length, 5)} pending strategies`);
      for (const s of needsValidation.slice(0, 5)) {
        lines.push(`  • ${s.name ?? s.strategy_id ?? "unknown"}`);
      }
    } else {
      lines.push(``, `✅ No pending blueprints — all strategies validated`);
    }

    await slack.call("slack_post_message", {
      channel_id: "C09F415GZ6W", // #quant-lab
      text: lines.join("\n"),
    });

    console.log(`Blueprint executor: ${capCount}/${totalCaps} caps, ${needsValidation.length} pending`);
  } catch (err) {
    console.error("Blueprint executor error:", err);
    await slack.call("slack_post_message", {
      channel_id: "C0AFT0GH54Z",
      text: `🚨 Blueprint executor failed: ${err}`,
    });
  } finally {
    await rt.close();
  }
}

main();
