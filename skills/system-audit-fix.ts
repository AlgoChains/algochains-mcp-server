// system-audit-fix.ts — Full-stack audit: health, risk, connectivity
// Replaces: multi-step bash health checks + python3 scripts
// Composes: algochains (platform_health, risk_alerts, broker_health) + slack
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Parallel health checks
    const [platformHealth, riskAlerts, brokers, supportedBrokers] =
      await Promise.all([
        ac.call("get_platform_health", {}),
        ac.call("check_risk_alerts", {}),
        ac.call("broker_health_check", {}),
        ac.call("get_supported_brokers", {}),
      ]);

    // 2. Check each connected broker
    const brokerStatuses: Array<{ name: string; status: string }> = [];
    const connectedBrokers = brokers?.brokers ?? supportedBrokers?.brokers ?? [];

    for (const b of connectedBrokers) {
      const name = typeof b === "string" ? b : b.name ?? b.id ?? "unknown";
      const status = typeof b === "string" ? "listed" : b.status ?? "unknown";
      brokerStatuses.push({ name, status });
    }

    // 3. Classify issues by severity
    const alerts = riskAlerts?.alerts ?? [];
    const critical = alerts.filter(
      (a: any) => a.severity === "critical" || a.severity === "P0"
    );
    const warnings = alerts.filter(
      (a: any) => a.severity === "warning" || a.severity === "P1" || a.severity === "high"
    );
    const info = alerts.filter(
      (a: any) =>
        a.severity !== "critical" &&
        a.severity !== "P0" &&
        a.severity !== "warning" &&
        a.severity !== "P1" &&
        a.severity !== "high"
    );

    // 4. Compose report
    const platformStatus = platformHealth?.status ?? "unknown";
    const statusEmoji =
      platformStatus === "operational" || platformStatus === "healthy"
        ? "✅"
        : "⚠️";

    const lines = [
      `🔍 *System Audit* — ${new Date().toISOString()}`,
      ``,
      `*Platform:* ${statusEmoji} ${platformStatus}`,
      `*Brokers:* ${brokerStatuses.length} checked`,
    ];

    if (brokerStatuses.length > 0) {
      for (const bs of brokerStatuses) {
        lines.push(`  • ${bs.name}: ${bs.status}`);
      }
    }

    lines.push(
      ``,
      `*Risk Alerts:* ${alerts.length} total`
    );

    if (critical.length > 0) {
      lines.push(`🚨 *Critical (${critical.length}):*`);
      for (const c of critical) {
        lines.push(`  • ${c.message ?? c.description ?? JSON.stringify(c)}`);
      }
    }

    if (warnings.length > 0) {
      lines.push(`⚠️ *Warnings (${warnings.length}):*`);
      for (const w of warnings) {
        lines.push(`  • ${w.message ?? w.description ?? JSON.stringify(w)}`);
      }
    }

    if (alerts.length === 0) {
      lines.push(`  ✅ No active alerts`);
    }

    // Post to appropriate channel based on severity
    const channel =
      critical.length > 0 ? "C0AFT0GH54Z" : "C09F415GZ6W"; // incident-response or quant-lab

    await slack.call("slack_post_message", {
      channel_id: channel,
      text: lines.join("\n"),
    });

    // If critical, also post to incident-response if we posted elsewhere
    if (critical.length > 0) {
      console.error(`CRITICAL: ${critical.length} critical alerts found`);
    }

    console.log(
      `Audit: platform=${platformStatus}, alerts=${alerts.length} (${critical.length} critical)`
    );
  } catch (err) {
    console.error("System audit error:", err);
    await slack.call("slack_post_message", {
      channel_id: "C0AFT0GH54Z",
      text: `🚨 System audit failed: ${err}`,
    });
  } finally {
    await rt.close();
  }
}

main();
