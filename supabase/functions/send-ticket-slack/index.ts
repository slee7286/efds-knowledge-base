import { messageBlocks, runOnce, SlackRejected, type Message, type Store, type TicketEvent, type Slack } from "./core.ts";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
});

export async function handler(req: Request): Promise<Response> {
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);
  const token = req.headers.get("X-EFDS-Worker-Token") ?? "";
  if (!/^[0-9a-f]{64}$/.test(token)) return json({ error: "Unauthorized" }, 401);
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const slackToken = Deno.env.get("SLACK_BOT_TOKEN");
  const websiteUrl = Deno.env.get("WEBSITE_URL") ?? "https://www.imperial-efds.com";
  if (!url || !key || !slackToken) return json({ error: "Worker is not configured" }, 503);
  // No caller-supplied URLs/credentials reach the database or Slack.
  const rpc = async (name: string, params: Record<string, unknown>) => {
    const response = await fetch(`${url}/rest/v1/rpc/${name}`, {
      method: "POST", headers: { apikey: key, Authorization: `Bearer ${key}`,
        "Content-Type": "application/json" },
      body: JSON.stringify(params), signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error("database_rpc_failed");
    return response.json();
  };
  try {
    if (await rpc("verify_ticket_slack_worker", { p_token: token }) !== true) {
      return json({ error: "Unauthorized" }, 401);
    }
    const store: Store = {
      async claim() { return await rpc("claim_ticket_slack_event", {}) as TicketEvent | null; },
      async finish(event, ts, rootTs) {
        await rpc("finish_ticket_slack_event", {
          p_event_id: event.event_id, p_ts: ts, p_root_ts: rootTs,
        });
      },
      async defer(event, uncertain) {
        await rpc("defer_ticket_slack_event", {
          p_event_id: event.event_id, p_uncertain: uncertain || event.reconcile_only,
        });
      },
    };
    const slackRequest = async (method: string, body: Record<string, unknown> | null) => {
      const endpoint = new URL(`https://slack.com/api/${method}`);
      if (!body) throw new Error("missing_read_parameters");
      const read = method.startsWith("conversations.");
      if (read) for (const [k, v] of Object.entries(body)) {
        if (v !== null && v !== undefined) endpoint.searchParams.set(k, String(v));
      }
      const response = await fetch(endpoint, {
        method: read ? "GET" : "POST",
        headers: { Authorization: `Bearer ${slackToken}`,
          Accept: "application/json", "Content-Type": "application/json; charset=utf-8" },
        ...(read ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(8000),
      });
      // HTTP error, timeout or malformed JSON has no definitive POST outcome.
      if (!response.ok) throw new Error("slack_http_failed");
      const data = await response.json();
      if (!data || typeof data !== "object") throw new Error("slack_invalid_response");
      if (data.ok !== true) {
        if (read) throw new Error("slack_read_rejected");
        throw new SlackRejected("Slack rejected request");
      }
      return data;
    };
    const messages = async (method: string, args: Record<string, unknown>): Promise<Message[]> => {
      const found: Message[] = [];
      let cursor: string | undefined;
      do {
        const result = await slackRequest(method, { ...args, limit: 200, cursor });
        if (!Array.isArray(result.messages)) throw new Error("slack_missing_messages");
        for (const msg of result.messages) {
          if (typeof msg.ts === "string" && typeof msg.text === "string") found.push(msg);
        }
        cursor = result.response_metadata?.next_cursor || undefined;
      } while (cursor);
      return found;
    };
    const slack: Slack = {
      history(channel) { return messages("conversations.history", { channel }); },
      replies(channel, rootTs) { return messages("conversations.replies", { channel, ts: rootTs }); },
      async post(channel, text, threadTs, eventId) {
        const result = await slackRequest("chat.postMessage", { channel, text, thread_ts: threadTs,
          blocks: messageBlocks(text, eventId), parse: "none", link_names: false, unfurl_links: false, unfurl_media: false });
        if (typeof result.ts !== "string" || !/^[0-9]+[.][0-9]+$/.test(result.ts)) {
          throw new Error("slack_timestamp_missing");
        }
        return result.ts;
      },
      async update(channel, ts, text, eventId) {
        await slackRequest("chat.update", {
          channel, ts, text, blocks: messageBlocks(text, eventId, true), parse: "none", link_names: false,
        });
      },
    };
    let processed = 0;
    let attention = 0;
    for (let i = 0; i < 10; i++) {
      const outcome = await runOnce(store, slack, websiteUrl);
      if (outcome === "empty") break;
      processed++;
      if (outcome === "retry" || outcome === "uncertain") attention++;
    }
    console.info(JSON.stringify({ event: "ticket_slack_batch", processed, attention }));
    return json({ processed, attention });
  } catch {
    // Never log exception bodies: fetch failures may include credentials.
    console.error("ticket_slack_worker_failed");
    return json({ error: "Ticket Slack worker could not process the queue" }, 503);
  }
}

if (import.meta.main) Deno.serve(handler);
