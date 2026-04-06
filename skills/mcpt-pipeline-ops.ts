// mcpt-pipeline-ops.ts — MCPT marketplace pipeline operations
// Replaces: python3 scripts/mcpt_autopilot.py
// Composes: algochains (backtest + validate + marketplace) + slack
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Browse marketplace for candidates needing validation
    const marketplace = await ac.call("browse_strategy_marketplace", {
      min_sharpe: 2.0,
    });
    const bots = marketplace?.strategies ?? marketplace?.bots ?? [];

    // 2. Run MCPT decay check on top candidates
    const decayResults: Array<{ name: string; status: string; sharpe: number }> = [];
    const candidates = bots.slice(0, 10);

    for (const bot of candidates) {
      try {
        const validation = await ac.call("validate_strategy", {
          strategy_id: bot.strategy_id ?? bot.id,
        });
        decayResults.push({
          name: bot.name ?? bot.strategy_id ?? "unknown",
          status: validation?.status ?? "unknown",
          sharpe: validation?.oos_sharpe ?? bot.sharpe ?? 0,
        });
      } catch {
        decayResults.push({
          name: bot.name ?? "unknown",
          status: "error",
          sharpe: 0,
        });
      }
    }

    // 3. Identify bots needing paper trading graduation
    const graduating = decayResults.filter(
      (r) => r.status === "validated" && r.sharpe >= 2.5
    );
    const decayed = decayResults.filter(
      (r) => r.status === "decayed" || r.sharpe < 1.5
    );

    // 4. Compose report
    const lines = [
      `🏭 *MCPT Pipeline Report* — ${new Date().toLocaleDateString()}`,
      ``,
      `*Marketplace:* ${bots.length} total bots`,
      `*Checked:* ${decayResults.length} candidates`,
      `*Graduating:* ${graduating.length} ready for paper trading`,
      `*Decayed:* ${decayed.length} need review`,
    ];

    if (graduating.length > 0) {
      lines.push(``, `*Ready to Graduate:*`);
      for (const g of graduating) {
        lines.push(`  • ${g.name} — Sharpe ${g.sharpe.toFixed(2)}`);
      }
    }

    if (decayed.length > 0) {
      lines.push(``, `*Decayed (review needed):*`);
      for (const d of decayed) {
        lines.push(`  • ${d.name} — Sharpe ${d.sharpe.toFixed(2)} (${d.status})`);
      }
    }

    await slack.call("slack_post_message", {
      channel_id: "C09F415GZ6W", // #quant-lab
      text: lines.join("\n"),
    });

    console.log(`MCPT pipeline: ${graduating.length} graduating, ${decayed.length} decayed`);
  } catch (err) {
    console.error("MCPT pipeline error:", err);
    await slack.call("slack_post_message", {
      channel_id: "C0AFT0GH54Z", // #incident-response
      text: `🚨 MCPT pipeline failed: ${err}`,
    });
  } finally {
    await rt.close();
  }
}

main();
