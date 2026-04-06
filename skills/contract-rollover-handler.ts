// contract-rollover-handler.ts — Detect expiring futures contracts and alert
// Replaces: python3 contract rollover detection scripts
// Composes: algochains (positions, account) + massive (contract data) + slack
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const massive = createServerProxy(rt, "massive");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Get current futures positions
    const positions = await ac.call("get_positions", {
      broker: "tradovate",
    });

    const openPositions = positions?.positions ?? [];
    if (openPositions.length === 0) {
      console.log("No open futures positions — skipping rollover check");
      await rt.close();
      return;
    }

    // 2. Check each position for contract expiry
    const futuresContracts = ["MNQ", "MES", "NQ", "ES", "CL"];
    const expiryAlerts: Array<{
      symbol: string;
      daysToExpiry: number;
      action: string;
    }> = [];

    for (const pos of openPositions) {
      const symbol = pos.symbol ?? pos.contract ?? "";
      const isFutures = futuresContracts.some((fc) =>
        symbol.toUpperCase().includes(fc)
      );

      if (!isFutures) continue;

      // Fetch contract details via Massive
      try {
        const contractInfo = await massive.call("call_api", {
          method: "GET",
          path: `/v3/reference/tickers/${symbol}`,
        });

        const expiryDate = contractInfo?.results?.expiration_date;
        if (expiryDate) {
          const expiry = new Date(expiryDate);
          const now = new Date();
          const daysToExpiry = Math.ceil(
            (expiry.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)
          );

          if (daysToExpiry <= 7) {
            expiryAlerts.push({
              symbol,
              daysToExpiry,
              action:
                daysToExpiry <= 1
                  ? "ROLL NOW — expires tomorrow"
                  : daysToExpiry <= 3
                    ? "ROLL SOON — expires in " + daysToExpiry + " days"
                    : "PLAN ROLL — expires in " + daysToExpiry + " days",
            });
          }
        }
      } catch {
        // Contract info unavailable — flag for manual check
        expiryAlerts.push({
          symbol,
          daysToExpiry: -1,
          action: "MANUAL CHECK — contract info unavailable",
        });
      }
    }

    // 3. Post alerts if any contracts expiring within 7 days
    if (expiryAlerts.length === 0) {
      console.log("No contracts expiring within 7 days");
      await rt.close();
      return;
    }

    const urgent = expiryAlerts.filter((a) => a.daysToExpiry <= 3 && a.daysToExpiry >= 0);
    const channel = urgent.length > 0 ? "C0AFT0GH54Z" : "C09TGL20N4V";

    const lines = [
      `🔄 *Contract Rollover Alert* — ${new Date().toLocaleDateString()}`,
      ``,
      `*${expiryAlerts.length} contract(s) expiring within 7 days:*`,
    ];

    for (const alert of expiryAlerts) {
      const emoji =
        alert.daysToExpiry <= 1
          ? "🚨"
          : alert.daysToExpiry <= 3
            ? "⚠️"
            : "📅";
      lines.push(`  ${emoji} *${alert.symbol}:* ${alert.action}`);
    }

    lines.push(
      ``,
      `*Action Required:* Close expiring contracts and open next-month positions.`
    );

    await slack.call("slack_post_message", {
      channel_id: channel,
      text: lines.join("\n"),
    });

    // Also alert incident-response if any are urgent
    if (urgent.length > 0 && channel !== "C0AFT0GH54Z") {
      await slack.call("slack_post_message", {
        channel_id: "C0AFT0GH54Z",
        text: `🚨 URGENT: ${urgent.length} futures contract(s) expiring within 3 days — rollover required`,
      });
    }

    console.log(`Rollover: ${expiryAlerts.length} alerts (${urgent.length} urgent)`);
  } catch (err) {
    console.error("Contract rollover error:", err);
    await slack.call("slack_post_message", {
      channel_id: "C0AFT0GH54Z",
      text: `🚨 Contract rollover check failed: ${err}`,
    });
  } finally {
    await rt.close();
  }
}

main();
