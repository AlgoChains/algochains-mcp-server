export type SlackBlock = {
  type: string;
  text?: { type: string; text: string; emoji?: boolean };
  fields?: Array<{ type: string; text: string }>;
  elements?: Array<{ type: string; text: string }>;
};

export type WaitlistFields = {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  broker: string;
  useCase: string;
  referral: string | null;
  position?: number;
  status: string;
  createdAt: string;
  source: string;
  details: Array<{ label: string; value: string }>;
};

export type RuntimeConfig = {
  notifySecret: string;
  slackBotToken: string;
  channelId: string;
  slackFetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
};

const DEFAULT_CHANNEL_ID = "C0A075W6D4H";
const TEST_PATTERNS = [
  /@test\.com$/i,
  /@example\.com$/i,
  /@mailinator\.com$/i,
  /@guerrillamail\.com$/i,
  /\+test/i,
  /^(test|admin|demo|noreply|no-reply)@/i,
];

export function loadConfig(): RuntimeConfig {
  return {
    notifySecret: Deno.env.get("WAITLIST_NOTIFY_SECRET") ?? "",
    slackBotToken: Deno.env.get("SLACK_BOT_TOKEN") ?? "",
    channelId: Deno.env.get("SLACK_CHANNEL_ID") ?? DEFAULT_CHANNEL_ID,
  };
}

function asString(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function parseName(
  record: Record<string, unknown>,
): { firstName: string; lastName: string } {
  const firstName = asString(record.first_name ?? record.firstName);
  const lastName = asString(record.last_name ?? record.lastName);
  if (firstName || lastName) {
    return { firstName, lastName };
  }

  const parts = asString(record.name).trim().split(/\s+/).filter(Boolean);
  return {
    firstName: parts[0] ?? "",
    lastName: parts.slice(1).join(" "),
  };
}

export function extractFields(record: Record<string, unknown>): WaitlistFields {
  const { firstName, lastName } = parseName(record);
  const positionRaw = record.position ?? record.waitlist_position ??
    record.rank;
  const position = typeof positionRaw === "number"
    ? positionRaw
    : Number.isFinite(Number(positionRaw))
    ? Number(positionRaw)
    : undefined;

  const fields = {
    id: asString(record.id),
    email: asString(record.email ?? record.email_address),
    firstName,
    lastName,
    broker:
      asString(record.broker_interest ?? record.broker ?? record.broker_name) ||
      "--",
    useCase: asString(record.use_case ?? record.useCase ?? record.notes).slice(
      0,
      200,
    ),
    referral: asString(record.referral_code ?? record.referral) || null,
    position,
    status: asString(record.status) || "waiting",
    createdAt:
      asString(record.created_at ?? record.date_joined ?? record.inserted_at) ||
      new Date().toISOString(),
    source: asString(record.source),
  };

  return {
    ...fields,
    details: buildDetails(record, fields),
  };
}

export function isTestRow(record: Record<string, unknown>): boolean {
  const fields = extractFields(record);
  const status = fields.status.toLowerCase();
  return TEST_PATTERNS.some((pattern) => pattern.test(fields.email)) ||
    status === "test" ||
    status === "dev" ||
    record.is_test === true;
}

export function maskEmail(email: string): string {
  return email;
}

function humanizeKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function truncateForSlack(value: string, maxLength = 500): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
}

function buildDetails(
  record: Record<string, unknown>,
  fields: Omit<WaitlistFields, "details">,
): Array<{ label: string; value: string }> {
  const preferredKeys = [
    "id",
    "username",
    "source",
    "status",
    "selected_for_beta",
    "user_id",
    "clerk_waitlist_entry_id",
    "clerk_waitlist_synced_at",
    "clerk_waitlist_sync_error",
    "confirmation_email_sent_at",
    "confirmation_email_error",
    "created_at",
    "updated_at",
    "date_joined",
  ];
  const consumed = new Set([
    "email",
    "email_address",
    "name",
    "first_name",
    "firstName",
    "last_name",
    "lastName",
    "broker_interest",
    "broker",
    "broker_name",
    "use_case",
    "useCase",
    "notes",
    "referral_code",
    "referral",
    "position",
    "waitlist_position",
    "rank",
  ]);
  const seen = new Set<string>();
  const details: Array<{ label: string; value: string }> = [];

  for (const key of [...preferredKeys, ...Object.keys(record).sort()]) {
    if (seen.has(key) || consumed.has(key)) continue;
    seen.add(key);
    const raw = record[key];
    if (raw === null || raw === undefined || raw === "") continue;
    details.push({
      label: humanizeKey(key),
      value: truncateForSlack(asString(raw)),
    });
  }

  if (fields.source && !details.some((detail) => detail.label === "Source")) {
    details.push({ label: "Source", value: fields.source });
  }

  return details;
}

function sourceLabel(tableName: string): string {
  if (tableName === "algochains_waitlist") return "Platform (algochains.ai)";
  if (tableName === "home_betawaitlist") return "Django beta waitlist";
  if (tableName === "home_waitlist") return "Legacy Django waitlist";
  return tableName || "waitlist";
}

export function buildBlocks(
  fields: WaitlistFields,
  tableName: string,
): SlackBlock[] {
  const name = [fields.firstName, fields.lastName].filter(Boolean).join(" ") ||
    "--";
  const createdAt = new Date(fields.createdAt);
  const timestamp = Number.isNaN(createdAt.getTime())
    ? fields.createdAt
    : createdAt.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "America/Los_Angeles",
    });

  const blocks: SlackBlock[] = [
    {
      type: "header",
      text: {
        type: "plain_text",
        text: "New AlgoChains Waitlist Signup",
        emoji: true,
      },
    },
    {
      type: "section",
      fields: [
        { type: "mrkdwn", text: `*Name*\n${name}` },
        { type: "mrkdwn", text: `*Email*\n${fields.email || "--"}` },
        {
          type: "mrkdwn",
          text: `*Position*\n${
            fields.position ? `#${fields.position} in line` : "--"
          }`,
        },
        { type: "mrkdwn", text: `*Broker interest*\n${fields.broker || "--"}` },
      ],
    },
  ];

  if (fields.useCase) {
    blocks.push({
      type: "section",
      text: {
        type: "mrkdwn",
        text: `*What they want to do*\n_${fields.useCase}_`,
      },
    });
  }

  if (fields.referral) {
    blocks.push({
      type: "section",
      text: { type: "mrkdwn", text: `*Referred by*  \`${fields.referral}\`` },
    });
  }

  if (fields.details.length > 0) {
    blocks.push({
      type: "section",
      text: {
        type: "mrkdwn",
        text: "*Associated signup details*\n" +
          fields.details
            .map((detail) => `*${detail.label}:* ${detail.value}`)
            .join("\n")
            .slice(0, 2900),
      },
    });
  }

  blocks.push(
    {
      type: "context",
      elements: [
        {
          type: "mrkdwn",
          text: `Source: ${
            sourceLabel(tableName)
          } | Status: ${fields.status} | ${timestamp} PT`,
        },
      ],
    },
    { type: "divider" },
  );

  return blocks;
}

export function createDuplicateGuard(
  ttlMs = 10 * 60 * 1000,
  now = () => Date.now(),
) {
  const posted = new Map<string, number>();
  return {
    isDuplicate(id: string): boolean {
      const lastSeen = posted.get(id);
      const current = now();
      if (lastSeen && current - lastSeen < ttlMs) {
        return true;
      }
      posted.set(id, current);
      for (const [key, seenAt] of posted) {
        if (current - seenAt > ttlMs) {
          posted.delete(key);
        }
      }
      return false;
    },
  };
}

const duplicateGuard = createDuplicateGuard();

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function postToSlack(
  payload: object,
  config: RuntimeConfig,
): Promise<boolean> {
  const slackFetch = config.slackFetch ?? fetch;
  const requestInit: RequestInit = {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${config.slackBotToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  };

  let response = await slackFetch(
    "https://slack.com/api/chat.postMessage",
    requestInit,
  );
  if (response.status === 429) {
    const retryAfterSeconds = Number.parseInt(
      response.headers.get("Retry-After") ?? "2",
      10,
    );
    const delayMs = Math.min(Math.max(retryAfterSeconds, 1) * 1000, 10_000);
    await (config.sleep ?? sleep)(delayMs);
    response = await slackFetch(
      "https://slack.com/api/chat.postMessage",
      requestInit,
    );
  }

  const body = await response.json().catch(() => ({
    ok: false,
    error: "invalid_json",
  }));
  if (!body.ok) {
    console.error(
      "[waitlist-notify] Slack error:",
      body.error ?? response.status,
    );
  }
  return body.ok === true;
}

export async function handleWaitlistWebhook(
  request: Request,
  config = loadConfig(),
  guard = duplicateGuard,
): Promise<Response> {
  if (
    !config.notifySecret ||
    (request.headers.get("x-waitlist-secret") ?? "") !== config.notifySecret
  ) {
    return new Response("Unauthorized", { status: 401 });
  }

  const payload = await request.json().catch(() => null);
  if (!payload || payload.type !== "INSERT") {
    return new Response("ignored", { status: 200 });
  }

  const record = (payload.record ?? {}) as Record<string, unknown>;
  const tableName = asString(payload.table);
  if (isTestRow(record)) {
    console.log(
      "[waitlist-notify] filtered test row:",
      extractFields(record).email,
    );
    return new Response("filtered", { status: 200 });
  }

  const fields = extractFields(record);
  const dedupKey = fields.id ? `${tableName}:${fields.id}` : "";
  if (dedupKey && guard.isDuplicate(dedupKey)) {
    console.log("[waitlist-notify] duplicate record id:", dedupKey);
    return new Response("already_sent", { status: 200 });
  }

  const fallback = `New signup: ${
    fields.firstName || fields.email || "unknown"
  } | ${fields.broker || "no broker"} | #${fields.position ?? "?"}`;

  const ok = await postToSlack({
    channel: config.channelId,
    text: fallback,
    blocks: buildBlocks(fields, tableName),
    unfurl_links: false,
  }, config);

  return ok
    ? new Response("ok", { status: 200 })
    : new Response("slack_error", { status: 500 });
}
