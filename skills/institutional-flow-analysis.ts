// institutional-flow-analysis.ts — Options flow + dark pool + sentiment analysis
// Replaces: python3 institutional_flow_integrator.py + manual grep chains
// Composes: algochains (order_flow, sentiment, regime) + massive (market data) + slack
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const massive = createServerProxy(rt, "massive");
  const slack = createServerProxy(rt, "slack");

  try {
    const watchlist = ["NVDA", "AAPL", "MSFT", "TSLA", "SPY"];

    // 1. Parallel: regime + sentiment for each symbol
    const [regime, ...sentiments] = await Promise.all([
      ac.call("detect_market_regime", {}),
      ...watchlist.map((sym) =>
        ac.call("analyze_sentiment", { symbol: sym }).catch(() => ({
          symbol: sym,
          sentiment: "unavailable",
        }))
      ),
    ]);

    // 2. Fetch options flow / unusual activity via Massive
    const flowResults: Array<{
      symbol: string;
      volume: number | null;
      change: number | null;
    }> = [];

    for (const sym of watchlist) {
      try {
        const snap = await massive.call("call_api", {
          method: "GET",
          path: `/v2/aggs/ticker/${sym}/prev`,
        });
        const result = snap?.results?.[0];
        flowResults.push({
          symbol: sym,
          volume: result?.v ?? null,
          change: result?.c && result?.o
            ? ((result.c - result.o) / result.o) * 100
            : null,
        });
      } catch {
        flowResults.push({ symbol: sym, volume: null, change: null });
      }
    }

    // 3. Check risk alerts for institutional-level signals
    const riskAlerts = await ac.call("check_risk_alerts", {});
    const alerts = riskAlerts?.alerts ?? [];

    // 4. Compose institutional flow report
    const regimeLabel = regime?.regime ?? regime?.state ?? "unknown";
    const lines = [
      `🏛️ *Institutional Flow Analysis* — ${new Date().toLocaleDateString()}`,
      ``,
      `*Market Regime:* ${regimeLabel}`,
      ``,
      `*Watchlist Signals:*`,
    ];

    for (let i = 0; i < watchlist.length; i++) {
      const sym = watchlist[i];
      const sent = sentiments[i];
      const flow = flowResults.find((f) => f.symbol === sym);

      const sentLabel =
        sent?.sentiment ?? sent?.overall_sentiment ?? "N/A";
      const changeStr =
        flow?.change != null ? `${flow.change >= 0 ? "+" : ""}${flow.change.toFixed(2)}%` : "N/A";
      const volStr =
        flow?.volume != null
          ? `${(flow.volume / 1_000_000).toFixed(1)}M`
          : "N/A";

      lines.push(
        `  *${sym}:* ${changeStr} | Vol ${volStr} | Sentiment: ${sentLabel}`
      );
    }

    if (alerts.length > 0) {
      lines.push(``, `*Active Risk Alerts:* ${alerts.length}`);
      for (const a of alerts.slice(0, 3)) {
        lines.push(`  ⚠️ ${a.message ?? a.description ?? JSON.stringify(a)}`);
      }
    }

    await slack.call("slack_post_message", {
      channel_id: "C09F415GZ6W", // #quant-lab
      text: lines.join("\n"),
    });

    console.log(`Institutional flow: regime=${regimeLabel}, ${watchlist.length} symbols analyzed`);
  } catch (err) {
    console.error("Institutional flow error:", err);
    await slack.call("slack_post_message", {
      channel_id: "C0AFT0GH54Z",
      text: `🚨 Institutional flow analysis failed: ${err}`,
    });
  } finally {
    await rt.close();
  }
}

main();
