// Shared helpers for AlgoChains edge functions.

export interface SlackPostOptions {
  webhook: string;
  text: string;
  username?: string;
  emoji?: string;
}

export async function postSlack(opts: SlackPostOptions): Promise<boolean> {
  if (!opts.webhook) {
    console.warn("postSlack: webhook missing — skipping");
    return false;
  }
  try {
    const res = await fetch(opts.webhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: opts.text,
        username: opts.username || "algochains-edge",
        icon_emoji: opts.emoji || ":satellite_antenna:",
      }),
    });
    if (!res.ok) {
      console.warn("postSlack non-200:", res.status, await res.text());
      return false;
    }
    return true;
  } catch (err) {
    console.error("postSlack failed:", err);
    return false;
  }
}

export function isoNow(): string {
  return new Date().toISOString();
}

export function envOrThrow(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing required env: ${name}`);
  return v;
}
