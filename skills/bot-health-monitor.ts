/**
 * bot-health-monitor.ts — MCPorter multi-MCP composition
 * Replaces bash bot health checks with typed TypeScript.
 *
 * Schedule: */5 * * * * (every 5 minutes during market hours)
 * Run: npx tsx skills/bot-health-monitor.ts
 */
import { createRuntime, createServerProxy } from "mcporter";

const BOTS = [
  { name: "MNQ Scalper", process: "FUTURES_SCALPER", broker: "tradovate" },
  { name: "CL Scalper", process: "CL_FUTURES", broker: "tradovate" },
  { name: "MES Swing", process: "mes_swing", broker: "tradovate" },
  { name: "NQ Swing", process: "nq_swing", broker: "tradovate" },
];

interface BotStatus {
  name: string;
  healthy: boolean;
  positions: number;
  issue?: string;
}

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const slack = createServerProxy(rt, "slack");

  const statuses: BotStatus[] = [];
  let hasIssue = false;

  try {
    // 1. Check account health
    const account = await ac.call("get_account", { broker: "tradovate" });
    const equity = account?.equity ?? 0;

    // 2. Check positions for each bot
    const positions = await ac.call("get_positions", { broker: "tradovate" });
    const posCount = positions?.positions?.length ?? 0;

    // 3. Platform health
    const health = await ac.call("get_platform_health", {});

    // 4. Risk alerts
    const risks = await ac.call("check_risk_alerts", {
      portfolio: { broker: "tradovate", positions: positions?.positions ?? [] },
    });

    // 5. Market regime for context
    const regime = await ac.call("detect_market_regime", {});

    // 6. Build status report
    const triggered = risks?.triggered ?? 0;
    const regimeName = regime?.regime ?? "unknown";

    const lines: string[] = [
      `*Bot Health Check* — ${new Date().toLocaleTimeString("en-US")}`,
      ``,
      `*Account:* $${equity} equity | ${posCount} positions`,
      `*Regime:* ${regimeName} | Risk alerts: ${triggered}`,
      `*Platform:* ${health?.status ?? "unknown"}`,
    ];

    if (triggered > 0) {
      hasIssue = true;
      lines.push(``, `*Risk Alerts Triggered:*`);
      for (const alert of risks?.alerts ?? []) {
        lines.push(`  - ${alert.rule}: ${alert.message}`);
      }
    }

    // 7. Post to appropriate channel
    if (hasIssue) {
      // P1+ issues go to #incident-response
      await slack.call("slack_post_message", {
        channel_id: "C0AFT0GH54Z", // #incident-response
        text: lines.join("\n"),
      });
    }

    // Always post health summary to #tradovate-futures-bot-changelog
    await slack.call("slack_post_message", {
      channel_id: "C09TGL20N4V", // #tradovate-futures-bot-changelog
      text: lines.join("\n"),
    });

    console.log(`Health check complete: ${hasIssue ? "ISSUES FOUND" : "ALL HEALTHY"}`);
  } catch (err) {
    console.error("Health monitor failed:", err);
    try {
      await slack.call("slack_post_message", {
        channel_id: "C0AFT0GH54Z",
        text: `Bot Health Monitor FAILED: ${err instanceof Error ? err.message : String(err)}`,
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
