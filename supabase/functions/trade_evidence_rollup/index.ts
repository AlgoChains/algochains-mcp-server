// trade_evidence_rollup edge function.
//
// Requires header: x-rollup-secret matching TRADE_EVIDENCE_ROLLUP_SECRET.
// Cron job trade_evidence_rollup_daily must send the same secret.
//
// Deploy:
//   supabase secrets set TRADE_EVIDENCE_ROLLUP_SECRET=<value>
//   supabase functions deploy trade_evidence_rollup --no-verify-jwt
//
// Prefer enabling verify_jwt once cron uses a service_role JWT instead of a shared secret.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { postSlack, envOrThrow, isoNow } from "../_shared/slack.ts";

interface FillEnvSummary {
  broker_env: string;
  trade_day: string;
  total_signals: number;
  wins: number;
  avg_slippage_ticks: number | null;
  avg_pnl_usd: number | null;
  total_pnl_usd: number | null;
}

interface RecommendationRow {
  broker_env: string;
  status: string;
  patch_scope: string;
  created_at: string;
}

function authorized(req: Request): boolean {
  const expected = Deno.env.get("TRADE_EVIDENCE_ROLLUP_SECRET") ?? "";
  if (!expected) {
    console.error("[trade_evidence_rollup] TRADE_EVIDENCE_ROLLUP_SECRET is not set");
    return false;
  }
  const headerSecret = req.headers.get("x-rollup-secret") ?? "";
  const auth = req.headers.get("Authorization") ?? "";
  const bearer = auth.toLowerCase().startsWith("bearer ")
    ? auth.slice(7).trim()
    : "";
  return headerSecret === expected || bearer === expected;
}

Deno.serve(async (req) => {
  if (!authorized(req)) {
    return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const sb = createClient(
    envOrThrow("SUPABASE_URL"),
    envOrThrow("SUPABASE_SERVICE_ROLE_KEY"),
  );
  const labHook = Deno.env.get("SLACK_QUANT_LAB_WEBHOOK") || "";

  const since = new Date(Date.now() - 7 * 24 * 60 * 60_000).toISOString();

  const { data: fillRows, error: fillError } = await sb
    .from("v_fill_env_summary")
    .select(
      "broker_env,trade_day,total_signals,wins,avg_slippage_ticks,avg_pnl_usd,total_pnl_usd",
    )
    .gte("trade_day", since)
    .limit(50);

  if (fillError) {
    return new Response(JSON.stringify({ ok: false, error: fillError.message }), {
      status: 500,
    });
  }

  const { data: recRows, error: recError } = await sb
    .from("trade_recommendations")
    .select("broker_env,status,patch_scope,created_at")
    .gte("created_at", since)
    .limit(500);

  if (recError) {
    return new Response(JSON.stringify({ ok: false, error: recError.message }), {
      status: 500,
    });
  }

  const fills = (fillRows || []) as FillEnvSummary[];
  const recs = (recRows || []) as RecommendationRow[];

  const recSummary: Record<string, Record<string, number>> = {};
  for (const row of recs) {
    const env = row.broker_env || "unknown";
    const key = `${row.status}:${row.patch_scope}`;
    if (!recSummary[env]) recSummary[env] = {};
    recSummary[env][key] = (recSummary[env][key] || 0) + 1;
  }

  const out = {
    ts: isoNow(),
    window_days: 7,
    fill_rows: fills.length,
    recommendation_rows: recs.length,
    recommendations: recSummary,
  };

  if (labHook) {
    const lines = [
      ":clipboard: *trade_evidence_rollup* — 7d demo/live evidence",
      `Fill summary rows: ${fills.length}`,
      `Recommendation rows: ${recs.length}`,
    ];
    for (const row of fills.slice(0, 10)) {
      lines.push(
        `${row.broker_env} ${String(row.trade_day).slice(0, 10)}: ` +
          `signals=${row.total_signals} wins=${row.wins} ` +
          `avg_slip=${row.avg_slippage_ticks ?? "n/a"} ` +
          `total_pnl=${row.total_pnl_usd ?? "n/a"}`,
      );
    }
    for (const [env, summary] of Object.entries(recSummary)) {
      lines.push(
        `${env} recommendations: ` +
          Object.entries(summary).map(([k, v]) => `${k}=${v}`).join(", "),
      );
    }
    await postSlack({
      webhook: labHook,
      text: lines.join("\n"),
      emoji: ":clipboard:",
    });
  }

  return new Response(JSON.stringify(out, null, 2), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});
