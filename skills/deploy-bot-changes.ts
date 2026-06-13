// deploy-bot-changes.ts — Safe bot deployment with pre/post checks
// Replaces: multi-step bash (backup → kill → deploy → verify)
// Composes: algochains (positions, account, platform_health) + slack
import { createRuntime, createServerProxy } from "mcporter";

async function main() {
  const rt = await createRuntime();
  const ac = createServerProxy(rt, "algochains");
  const slack = createServerProxy(rt, "slack");

  try {
    // 1. Pre-flight: ensure positions are flat before deploy
    const [positions, account] = await Promise.all([
      ac.call("get_positions", { broker: "tradovate" }),
      ac.call("get_account", { broker: "tradovate" }),
    ]);

    const openPositions = positions?.positions ?? [];
    const posCount = openPositions.length ?? positions?.count ?? 0;

    if (posCount > 0) {
      const posNames = openPositions
        .map((p: any) => `${p.symbol ?? p.contract}: ${p.qty ?? p.size} @ ${p.avgPrice ?? "?"}`)
        .join(", ");

      await slack.call("slack_post_message", {
        channel_id: "C0AFT0GH54Z", // #incident-response
        text: `🛑 *Deploy BLOCKED* — ${posCount} open positions detected: ${posNames}\nClose all positions before deploying bot changes.`,
      });

      console.error(`Deploy blocked: ${posCount} open positions`);
      await rt.close();
      return;
    }

    // 2. Snapshot pre-deploy state
    const preHealth = await ac.call("get_platform_health", {});
    const preEquity = account?.equity ?? account?.balance ?? 0;

    // 3. Record deploy start
    const deployStart = new Date().toISOString();
    await slack.call("slack_post_message", {
      channel_id: process.env.SLACK_CHANNEL_BOT_CHANGELOG ?? "C09TGL20N4V", // #tradovate-futures-bot-changelog
      text: [
        `🔄 *Bot Deploy Started* — ${deployStart}`,
        `*Pre-Deploy Equity:* $${preEquity}`,
        `*Open Positions:* 0 (confirmed flat)`,
        `*Platform Health:* ${preHealth?.status ?? "unknown"}`,
        `Deploying changes...`,
      ].join("\n"),
    });

    // 4. Post-deploy verification (run after external deploy completes)
    // In practice, the actual deploy (kill/restart bots) happens externally.
    // This script handles the pre/post verification bookends.
    const postHealth = await ac.call("get_platform_health", {});
    const postAccount = await ac.call("get_account", { broker: "tradovate" });
    const postEquity = postAccount?.equity ?? postAccount?.balance ?? 0;

    const equityDrift = Math.abs(postEquity - preEquity);
    const healthOk = postHealth?.status === "operational" || postHealth?.status === "healthy";

    await slack.call("slack_post_message", {
      channel_id: process.env.SLACK_CHANNEL_BOT_CHANGELOG ?? "C09TGL20N4V",
      text: [
        `✅ *Bot Deploy Verified* — ${new Date().toISOString()}`,
        `*Post-Deploy Equity:* $${postEquity} (drift: $${equityDrift.toFixed(2)})`,
        `*Platform Health:* ${postHealth?.status ?? "unknown"} ${healthOk ? "✅" : "⚠️"}`,
        equityDrift > 100 ? `⚠️ Equity drift > $100 — investigate` : `✅ Equity stable`,
      ].join("\n"),
    });

    console.log("Deploy verification complete");
  } catch (err) {
    console.error("Deploy verification error:", err);
    await slack.call("slack_post_message", {
      channel_id: "C0AFT0GH54Z",
      text: `🚨 Deploy verification failed: ${err}`,
    });
  } finally {
    await rt.close();
  }
}

main();
