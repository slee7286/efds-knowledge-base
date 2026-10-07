import { formatRoot, formatEvent, messageBlocks, runOnce, SlackRejected, type TicketEvent, type Store, type Slack } from "./core.ts";
import { handler } from "./index.ts";

const assert = (value: unknown, message = "assertion failed") => { if (!value) throw new Error(message); };
const ticket = "11111111-1111-4111-8111-111111111111";
const eventId = "22222222-2222-4222-8222-222222222222";
const site = "https://www.imperial-efds.com";
const eventBlockId = (id: string) => `efds-ticket-event:${id}:0`;
const previewBlockId = (id: string) => `efds-ticket-preview:${id}:test:0`;
type TestBlock = { type: "section"; block_id: string; text?: { type: "mrkdwn"; text: string } };
const event: TicketEvent = {
  event_id: eventId, ticket_id: ticket, action: "committee_create",
  snapshot: { title: "ACTION-024 — Plan <review> & launch", execution_status: "open",
    assignees: ["Alice <@U123> & team"], due_at: "2026-11-05T12:00:00Z",
    description: "Coordinate the committee launch" },
  channel: "C0BQPDP5T44", root_ts: null, create_event_id: null, reconcile_only: false,
};

function setup(rows: TicketEvent[], rootSeed: string | null = null, rootEventSeed: string | null = null) {
  const messages: Array<{ ts: string; text: string; blocks?: TestBlock[] }> = [];
  const posts: Array<{ thread: string | null; text: string; blocks: TestBlock[] }> = [];
  const updates: string[] = [];
  const deferred: boolean[] = [];
  let root = rootSeed;
  let rootEvent = rootEventSeed;
  const store: Store = {
    async claim() {
      const row = rows.shift();
      return row ? { ...row, root_ts: root, create_event_id: rootEvent } : null;
    },
    async finish(row, _ts, rootTs) {
      if (row.action === "committee_create") { root = rootTs; rootEvent = row.event_id; }
    },
    async defer(_row, uncertain) { deferred.push(uncertain); },
  };
  const slack: Slack = {
    async history() { return [...messages]; },
    async replies() { return [...messages]; },
    async update(_channel, ts, text, id) {
      updates.push(text);
      const message = messages.find((item) => item.ts === ts);
      if (message) {
        message.text = text;
        message.blocks = [{ type: "section", block_id: previewBlockId(id), text: { type: "mrkdwn", text } }];
      }
    },
    async post(_channel, text, thread, id) {
      const blocks = [{ type: "section" as const, block_id: eventBlockId(id), text: { type: "mrkdwn" as const, text } }];
      posts.push({ thread, text, blocks });
      const ts = `1234.${String(posts.length).padStart(6, "0")}`;
      messages.push({ ts, text, blocks });
      return ts;
    },
  };
  return { store, slack, messages, posts, updates, deferred };
}

Deno.test("edge root and thread text omit internal IDs while retaining the trusted link", () => {
  const root = formatRoot(event, site);
  assert(root.includes("Plan &lt;review&gt; &amp; launch"));
  assert(root.includes("Alice &lt;@U123&gt; &amp; team"));
  assert(root.includes("Description: Coordinate the committee launch"));
  assert(root.includes(`<${site}/dashboard/tickets/${ticket}|Open on website>`));
  assert(!root.includes("[EFDS ticket:"));
  assert(root.includes("Due: 2026-11-05"));
  const reply = formatEvent({ ...event, action: "committee_status", snapshot: {
    ...event.snapshot, execution_status: "completed", assignees: ["@here <!channel>"],
    description: "@everyone <unsafe>" } });
  assert(reply.includes("Status changed") && reply.includes("Completed"));
  assert(!reply.includes("[EFDS ticket:"));
  assert(!reply.includes("@here") && !reply.includes("<!channel>"));
  assert(!formatRoot({ ...event, snapshot: { ...event.snapshot, description: "@everyone <unsafe>" } }, site).includes("@everyone"));
  let rejected = false;
  try { formatRoot(event, "https://untrusted.example/path"); } catch { rejected = true; }
  assert(rejected);
});

Deno.test("long message text is split into valid hidden-ID sections", () => {
  const text = `Description: ${"🚀".repeat(6200)}\nOpen`;
  const blocks = messageBlocks(text, eventId);
  assert(blocks.length > 1);
  assert(blocks.every((block) => Array.from(block.text.text).length <= 2800));
  assert(blocks.map((block) => block.text.text).join("") === text);
  assert(new Set(blocks.map((block) => block.block_id)).size === blocks.length);
  assert(blocks.every((block) => !block.text.text.includes(eventId)));
});

Deno.test("new ticket posts one root; change updates root and posts in its thread", async () => {
  const change = { ...event, action: "committee_status" as const,
    event_id: "33333333-3333-4333-8333-333333333333",
    snapshot: { ...event.snapshot, execution_status: "completed",
      description: "Updated committee launch plan" } };
  const test = setup([event, change]);
  assert(await runOnce(test.store, test.slack, site) === "posted");
  assert(await runOnce(test.store, test.slack, site) === "posted");
  assert(test.posts.length === 2 && test.posts[0].thread === null && test.posts[1].thread === "1234.000001");
  assert(test.updates.length === 1 && !test.updates[0].includes("[EFDS ticket:"));
  assert(test.updates[0].includes("Description: Updated committee launch plan"));
  assert(!test.posts[0].text.includes("[EFDS ticket:") && !test.posts[1].text.includes("[EFDS ticket:"));
  assert(test.posts[0].blocks[0].block_id === eventBlockId(eventId));
  assert(test.posts[1].blocks[0].block_id === eventBlockId(change.event_id));
  assert(test.messages[0].blocks?.[0].block_id === previewBlockId(change.event_id));
  assert(!test.updates[0].includes(`event:${change.event_id}]`));
  assert(test.posts[1].text.includes("Completed"));
});

Deno.test("reconciliation finds hidden block IDs without a second POST", async () => {
  const test = setup([event]);
  test.messages.push({ ts: "999.000001", text: formatRoot(event, site), blocks: [{ type: "section", block_id: eventBlockId(eventId) }] });
  assert(await runOnce(test.store, test.slack, site) === "reconciled");
  assert(test.posts.length === 0);
});

Deno.test("reconciliation remains compatible with previously posted visible markers", async () => {
  const test = setup([event]);
  test.messages.push({ ts: "999.000001", text: `[EFDS ticket:${ticket} event:${eventId}]` });
  assert(await runOnce(test.store, test.slack, site) === "reconciled");
  assert(test.posts.length === 0);
});

Deno.test("updated root preview cannot reconcile a missing thread reply", async () => {
  const change = { ...event, event_id: "33333333-3333-4333-8333-333333333333",
    action: "committee_status" as const, root_ts: "1234.000001", create_event_id: eventId, reconcile_only: true };
  const test = setup([change], change.root_ts, eventId);
  test.messages.push({ ts: change.root_ts, text: formatRoot(change, site),
    blocks: [{ type: "section", block_id: previewBlockId(change.event_id) }] });
  assert(await runOnce(test.store, test.slack, site) === "uncertain");
  assert(test.posts.length === 0 && test.deferred.join() === "true");
});

Deno.test("lost POST response and absent hidden ID on stale claim remain uncertain", async () => {
  const test = setup([event]);
  test.slack.post = async () => { test.posts.push({ thread: null, text: "", blocks: [] }); throw new Error("timeout"); };
  assert(await runOnce(test.store, test.slack, site) === "uncertain");
  assert(test.deferred.join() === "true" && test.posts.length === 1);
  const stale = setup([{ ...event, reconcile_only: true }]);
  assert(await runOnce(stale.store, stale.slack, site) === "uncertain");
  assert(stale.posts.length === 0 && stale.deferred.join() === "true");
});

Deno.test("definite Slack rejection and failed read defer safely", async () => {
  const rejection = setup([event]);
  rejection.slack.post = async () => { throw new SlackRejected("ratelimited"); };
  assert(await runOnce(rejection.store, rejection.slack, site) === "retry");
  assert(rejection.deferred.join() === "false");
  const read = setup([event]);
  read.slack.history = async () => { throw new Error("read failed"); };
  assert(await runOnce(read.store, read.slack, site) === "retry");
  assert(read.posts.length === 0 && read.deferred.join() === "false");
});

Deno.test("unauthorized wake-up does not claim or post", async () => {
  assert((await handler(new Request(site, { method: "GET" }))).status === 405);
  assert((await handler(new Request(site, { method: "POST" }))).status === 401);
  assert((await handler(new Request(site, { method: "POST", headers: { "X-EFDS-Worker-Token": "a".repeat(64) } }))).status === 503);
});

Deno.test("worker writes hidden block IDs while reading ordinary Slack history", async () => {
  const envKeys = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SLACK_BOT_TOKEN"];
  const previous = new Map(envKeys.map((key) => [key, Deno.env.get(key)]));
  const originalFetch = globalThis.fetch;
  let claimCount = 0;
  let historyWasRead = false;
  const posted: Record<string, unknown>[] = [];
  Deno.env.set("SUPABASE_URL", "https://supabase.example");
  Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key");
  Deno.env.set("SLACK_BOT_TOKEN", "test-slack-token");
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input instanceof URL ? input : new URL(String(input));
    if (url.pathname.endsWith("/verify_ticket_slack_worker")) return new Response("true");
    if (url.pathname.endsWith("/claim_ticket_slack_event")) {
      claimCount++;
      return new Response(JSON.stringify(claimCount === 1 ? event : null));
    }
    if (url.pathname.endsWith("/finish_ticket_slack_event")) return new Response("null");
    if (url.pathname.endsWith("/conversations.history")) {
      historyWasRead = true;
      return new Response(JSON.stringify({ ok: true, messages: [] }));
    }
    if (url.pathname.endsWith("/chat.postMessage")) {
      posted.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return new Response(JSON.stringify({ ok: true, ts: "1234.000001" }));
    }
    throw new Error(`unexpected request ${url.pathname}`);
  };
  try {
    const response = await handler(new Request(site, {
      method: "POST", headers: { "X-EFDS-Worker-Token": "a".repeat(64) },
    }));
    assert(response.status === 200);
    assert(historyWasRead);
    assert(posted.length === 1);
    const blocks = posted[0].blocks as Array<{ block_id: string; text: { text: string } }>;
    assert(blocks[0].block_id === eventBlockId(eventId));
    assert(blocks[0].text.text === posted[0].text);
    assert(!String(posted[0].text).includes("[EFDS ticket:"));
  } finally {
    globalThis.fetch = originalFetch;
    for (const [key, value] of previous) {
      if (value === undefined) Deno.env.delete(key);
      else Deno.env.set(key, value);
    }
  }
});
