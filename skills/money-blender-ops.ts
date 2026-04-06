// money-blender-ops.ts — Forex bot health + rolling WF optimizer diagnostics
// Replaces: grep + tail + python3 chains for Operation Money Blender
// Composes: algochains (positions, account) + massive (forex data) + slack
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const massive = createServerProxy(rt, "massive");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Forex positions and account health
    const [positions, account, platformHealth] = await Promise.all([
      ac.call("get_positions", { broker: "oanda" }),
      ac.call("get_account", { broker: "oanda" }),
      ac.call("get_platform_health", {}),
    ]);

    // 2. Fetch major forex pair snapshots via Massive
    const pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"];
    const forexData: Array<{ pair: string; close: number | null }> = [];

    for (const pair of pairs) {
      try {
        const snap = await massive.call("call_api", {
          method: "GET",
          path: `/v2/aggs/ticker/C:${pair}/prev`,
        });
        const close = snap?.results?.[0]?.c ?? null;
        forexData.push({ pair, close });
      } catch {
        forexData.push({ pair, close: null });
      }
    }

    // 3. Check risk alerts
    const riskAlerts = await ac.call("check_risk_alerts", {});

    // 4. Compose diagnostic report
    const posCount = positions?.positions?.length ?? positions?.count ?? 0;
    const equity = account?.equity ?? account?.balance ?? "N/A";
    const forexLines = forexData
      .map((f) => `  • ${f.pair}: ${f.close ?? "unavailable"}`)
      .join("\n");

    const alertCount = riskAlerts?.alerts?.length ?? 0;
    const alertFlag = alertCount > 0 ? `⚠️ ${alertCount} active alerts` : "✅ No alerts";

    const report = [
      `💱 *Money Blender Diagnostics* — ${new Date().toLocaleDateString()}`,
      ``,
      `*Oanda Account:* $${equity}`,
      `*Open Positions:* ${posCount}`,
      `*Risk Alerts:* ${alertFlag}`,
      ``,
      `*Forex Snapshots:*`,
      forexLines,
      ``,
      `*Platform Health:* ${platformHealth?.status ?? "unknown"}`,
    ].join("\n");

    await slack.call("slack_post_message", {
      channel_id: "C09F415GZ6W", // #quant-lab
      text: report,
    });

    console.log("Money Blender diagnostics posted");
  } catch (err) {
    console.error("Money Blender error:", err);
    await slack.call("slack_post_message", {
      channel_id: "C0AFT0GH54Z",
      text: `🚨 Money Blender diagnostics failed: ${err}`,
    });
  } finally {
    await rt.close();
  }
}

main();
