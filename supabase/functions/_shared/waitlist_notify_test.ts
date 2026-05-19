import { assertEquals } from "@std/assert";
import {
  buildBlocks,
  createDuplicateGuard,
  extractFields,
  handleWaitlistWebhook,
  isTestRow,
  maskEmail,
} from "./waitlist_notify.ts";

Deno.test("extractFields maps Django beta waitlist fields safely", () => {
  const fields = extractFields({
    id: 123,
    email: "newuser@example.org",
    name: "Ada Lovelace",
    notes: "Wants dashboard and portfolio metrics",
    source: "clerk",
    status: "waiting",
    created_at: "2026-05-12T04:41:36.187835+00:00",
  });

  assertEquals(fields.id, "123");
  assertEquals(fields.email, "newuser@example.org");
  assertEquals(fields.firstName, "Ada");
  assertEquals(fields.lastName, "Lovelace");
  assertEquals(fields.useCase, "Wants dashboard and portfolio metrics");
  assertEquals(fields.source, "clerk");
});

Deno.test("test rows are filtered before Slack posting", () => {
  assertEquals(isTestRow({ email: "test@test.com", status: "waiting" }), true);
  assertEquals(isTestRow({ email: "real@algochains.ai", status: "dev" }), true);
  assertEquals(
    isTestRow({ email: "real@algochains.ai", status: "waiting" }),
    false,
  );
});

Deno.test("duplicate guard suppresses the same record id inside ttl", () => {
  let now = 1_000;
  const guard = createDuplicateGuard(10_000, () => now);

  assertEquals(guard.isDuplicate("abc"), false);
  assertEquals(guard.isDuplicate("abc"), true);
  now += 11_000;
  assertEquals(guard.isDuplicate("abc"), false);
});

Deno.test("maskEmail returns full email for owner-visible signup posts", () => {
  assertEquals(maskEmail("person@gmail.com"), "person@gmail.com");
  assertEquals(maskEmail("not-an-email"), "not-an-email");
});

Deno.test("buildBlocks renders source and optional use case", () => {
  const blocks = buildBlocks(
    {
      id: "1",
      email: "person@gmail.com",
      firstName: "Pat",
      lastName: "Trader",
      broker: "Tradovate",
      useCase: "Automate futures strategies",
      referral: null,
      position: 12,
      status: "waiting",
      createdAt: "2026-05-12T04:41:36.187835+00:00",
      source: "clerk",
      details: [
        { label: "Username", value: "pat_trader" },
        { label: "Clerk Waitlist Entry Id", value: "wait_123" },
      ],
    },
    "home_betawaitlist",
  );

  assertEquals(blocks[0].type, "header");
  assertEquals(JSON.stringify(blocks).includes("Django beta waitlist"), true);
  assertEquals(
    JSON.stringify(blocks).includes("Automate futures strategies"),
    true,
  );
  assertEquals(JSON.stringify(blocks).includes("person@gmail.com"), true);
  assertEquals(JSON.stringify(blocks).includes("wait_123"), true);
});

Deno.test("handleWaitlistWebhook rejects missing secret without calling Slack", async () => {
  let called = false;
  const response = await handleWaitlistWebhook(
    new Request("https://edge.test", {
      method: "POST",
      body: JSON.stringify({
        type: "INSERT",
        table: "home_betawaitlist",
        record: {},
      }),
    }),
    {
      notifySecret: "expected",
      slackBotToken: "xoxb-test",
      channelId: "C0A075W6D4H",
      slackFetch: () => {
        called = true;
        return Promise.resolve(new Response(JSON.stringify({ ok: true })));
      },
    },
  );

  assertEquals(response.status, 401);
  assertEquals(called, false);
});

Deno.test("handleWaitlistWebhook filters test rows without calling Slack", async () => {
  let called = false;
  const response = await handleWaitlistWebhook(
    new Request("https://edge.test", {
      method: "POST",
      headers: { "x-waitlist-secret": "expected" },
      body: JSON.stringify({
        type: "INSERT",
        table: "home_betawaitlist",
        record: { id: 1, email: "test@test.com" },
      }),
    }),
    {
      notifySecret: "expected",
      slackBotToken: "xoxb-test",
      channelId: "C0A075W6D4H",
      slackFetch: () => {
        called = true;
        return Promise.resolve(new Response(JSON.stringify({ ok: true })));
      },
    },
  );

  assertEquals(await response.text(), "filtered");
  assertEquals(called, false);
});

Deno.test("handleWaitlistWebhook retries Slack once after 429", async () => {
  let calls = 0;
  const response = await handleWaitlistWebhook(
    new Request("https://edge.test", {
      method: "POST",
      headers: { "x-waitlist-secret": "expected" },
      body: JSON.stringify({
        type: "INSERT",
        table: "home_betawaitlist",
        record: { id: 1, email: "real@algochains.ai", name: "Real User" },
      }),
    }),
    {
      notifySecret: "expected",
      slackBotToken: "xoxb-test",
      channelId: "C0A075W6D4H",
      sleep: () => Promise.resolve(),
      slackFetch: () => {
        calls += 1;
        if (calls === 1) {
          return Promise.resolve(
            new Response("", {
              status: 429,
              headers: { "Retry-After": "1" },
            }),
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify({ ok: true, ts: "123.456" })),
        );
      },
    },
  );

  assertEquals(response.status, 200);
  assertEquals(calls, 2);
});

Deno.test("handleWaitlistWebhook dedups by table and id, not id alone", async () => {
  const guard = createDuplicateGuard();
  let calls = 0;
  const baseConfig = {
    notifySecret: "expected",
    slackBotToken: "xoxb-test",
    channelId: "C0A075W6D4H",
    slackFetch: () => {
      calls += 1;
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, ts: `123.${calls}` })),
      );
    },
  };

  const first = await handleWaitlistWebhook(
    new Request("https://edge.test", {
      method: "POST",
      headers: { "x-waitlist-secret": "expected" },
      body: JSON.stringify({
        type: "INSERT",
        table: "home_betawaitlist",
        record: { id: 1, email: "one@algochains.ai", name: "One User" },
      }),
    }),
    baseConfig,
    guard,
  );
  const second = await handleWaitlistWebhook(
    new Request("https://edge.test", {
      method: "POST",
      headers: { "x-waitlist-secret": "expected" },
      body: JSON.stringify({
        type: "INSERT",
        table: "home_waitlist",
        record: { id: 1, email: "two@algochains.ai", name: "Two User" },
      }),
    }),
    baseConfig,
    guard,
  );

  assertEquals(first.status, 200);
  assertEquals(second.status, 200);
  assertEquals(calls, 2);
});
