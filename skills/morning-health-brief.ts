/**
 * morning-health-brief.ts — MCPorter multi-MCP composition
 * Replaces 40+ lines of bash with typed, composable TypeScript.
 *
 * Schedule: 0 7 * * 1-5 (weekday mornings, 7 AM PT)
 * Run: npx tsx skills/morning-health-brief.ts
 */
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const massive = createServerProxy(rt, "massive");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Portfolio snapshot (parallel)
    const [tvPositions, tvAccount, alpPositions] = await Promise.all([
      ac.call("get_positions", { broker: "tradovate" }),
      ac.call("get_account", { broker: "tradovate" }),
      ac.call("get_positions", { broker: "alpaca" }),
    ]);

    // 2. Market regime (V18)
    const regime = await ac.call("detect_market_regime", {});

    // 3. Pre-market data via Massive
    const [spyPrev, vixPrev] = await Promise.all([
      massive.call("call_api", { method: "GET", path: "/v2/aggs/ticker/SPY/prev" }),
      massive.call("call_api", { method: "GET", path: "/v2/aggs/ticker/VIX/prev" }),
    ]);

    // 4. Marketplace pipeline status
    const marketplace = await ac.call("browse_strategy_marketplace", { min_sharpe: 2.0 });

    // 5. Platform health
    const health = await ac.call("get_platform_health", {});

    // 6. Compose brief
    const tvEquity = tvAccount?.equity ?? "N/A";
    const tvPosCount = tvPositions?.positions?.length ?? 0;
    const alpPosCount = alpPositions?.positions?.length ?? 0;
    const regimeName = regime?.regime ?? "unknown";
    const regimeConf = regime?.confidence ?? 0;
    const spyClose = spyPrev?.results?.[0]?.c ?? "N/A";
    const vixClose = vixPrev?.results?.[0]?.c ?? "N/A";
    const mpCount = marketplace?.count ?? 0;

    const brief = [
      `*Morning Health Brief* — ${new Date().toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}`,
      ``,
      `*Tradovate:* $${tvEquity} equity | ${tvPosCount} positions`,
      `*Alpaca Paper:* ${alpPosCount} positions`,
      `*Regime:* ${regimeName} (confidence: ${(regimeConf * 100).toFixed(0)}%)`,
      `*SPY Prev Close:* $${spyClose} | *VIX:* ${vixClose}`,
      `*Marketplace:* ${mpCount} bots with Sharpe >= 2.0`,
      `*Platform:* ${health?.status ?? "unknown"}`,
      ``,
      `_Strategies: ${regime?.recommended_strategies?.join(", ") ?? "N/A"}_`,
    ].join("\n");

    await slack.call("slack_post_message", {
      channel_id: "C09F415GZ6W", // #quant-lab
      text: brief,
    });

    console.log("Morning brief posted to #quant-lab");
  } catch (err) {
    console.error("Morning brief failed:", err);

    // Post failure alert to #incident-response
    try {
      await slack.call("slack_post_message", {
        channel_id: "C0AFT0GH54Z", // #incident-response
        text: `Morning Health Brief FAILED: ${err instanceof Error ? err.message : String(err)}`,
      });
    } catch {
      // Slack itself failed
    }

    process.exit(1);
  } finally {
    await rt.close();
  }
}

main();
