/**
 * strategy-vault-tracker.ts — MCPorter multi-MCP composition
 * Tracks marketplace strategies, detects new/changed bots, reports to Slack.
 *
 * Schedule: 0 8,20 * * * (twice daily: 8 AM, 8 PM)
 * Run: npx tsx skills/strategy-vault-tracker.ts
 */
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Fetch all marketplace strategies by asset class
    const [futures, forex, equities] = await Promise.all([
      ac.call("browse_strategy_marketplace", { asset_class: "futures" }),
      ac.call("browse_strategy_marketplace", { asset_class: "forex" }),
      ac.call("browse_strategy_marketplace", { asset_class: "equities" }),
    ]);

    const futuresCount = futures?.count ?? 0;
    const forexCount = forex?.count ?? 0;
    const equitiesCount = equities?.count ?? 0;
    const total = futuresCount + forexCount + equitiesCount;

    // 2. Filter high-quality bots (Sharpe >= 2.0)
    const highQuality = await ac.call("browse_strategy_marketplace", {
      min_sharpe: 2.0,
    });
    const hqCount = highQuality?.count ?? 0;

    // 3. Check for recently validated bots
    const recentBots = highQuality?.results?.filter((bot: any) => {
      const created = new Date(bot.created_at ?? 0);
      const dayAgo = new Date(Date.now() - 24 * 60 * 60 * 1000);
      return created > dayAgo;
    }) ?? [];

    // 4. Compose report
    const lines: string[] = [
      `*Strategy Vault Tracker* — ${new Date().toLocaleDateString("en-US")}`,
      ``,
      `*Marketplace Inventory:*`,
      `  Futures: ${futuresCount} | Forex: ${forexCount} | Equities: ${equitiesCount}`,
      `  *Total:* ${total} strategies`,
      `  *High Quality (Sharpe >= 2.0):* ${hqCount}`,
    ];

    if (recentBots.length > 0) {
      lines.push(``, `*New Bots (last 24h):*`);
      for (const bot of recentBots) {
        lines.push(
          `  - ${bot.name}: Sharpe ${bot.sharpe?.toFixed(2)} | WR ${(bot.win_rate * 100)?.toFixed(0)}% | ${bot.asset_class}`
        );
      }
    } else {
      lines.push(``, `_No new bots in last 24 hours_`);
    }

    // 5. Post to #quant-lab
    await slack.call("slack_post_message", {
      channel_id: "C09F415GZ6W", // #quant-lab
      text: lines.join("\n"),
    });

    console.log(`Vault tracked: ${total} total, ${hqCount} high-quality, ${recentBots.length} new`);
  } catch (err) {
    console.error("Strategy vault tracker failed:", err);
    process.exit(1);
  } finally {
    await rt.close();
  }
}

main();
