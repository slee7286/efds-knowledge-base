// Same hidden block IDs, preview and fail-closed delivery semantics as the Python publisher.
export type TicketEvent = {
  event_id: string; ticket_id: string;
  action: "committee_create" | "committee_assign" | "committee_status" | "committee_update";
  snapshot: { title: string; execution_status: string; assignees: string[]; due_at: string | null; description?: string | null };
  channel: string; root_ts: string | null; create_event_id: string | null;
  reconcile_only: boolean;
};
export type Message = {
  ts: string; text: string;
  blocks?: Array<{ block_id?: string }>;
};
export type MessageBlock = {
  type: "section";
  block_id: string;
  text: { type: "mrkdwn"; text: string };
};
export type Store = {
  claim(): Promise<TicketEvent | null>;
  finish(event: TicketEvent, ts: string, rootTs: string): Promise<void>;
  defer(event: TicketEvent, uncertain: boolean): Promise<void>;
};
export type Slack = {
  history(channel: string): Promise<Message[]>;
  replies(channel: string, rootTs: string): Promise<Message[]>;
  post(channel: string, text: string, threadTs: string | null, eventId: string): Promise<string>;
  update(channel: string, ts: string, text: string, eventId: string): Promise<void>;
};
export class SlackRejected extends Error {}

const status: Record<string, string> = { open: "Open", in_progress: "In progress", blocked: "Blocked",
  completed: "Completed", cancelled: "Cancelled" };
const actions: Record<TicketEvent["action"], string> = {
  committee_create: "Created", committee_assign: "Assignment changed",
  committee_status: "Status changed", committee_update: "Details updated",
};
const safe = (value: unknown) => String(value ?? "").replace(/[\r\n]/g, " ")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/@(here|channel|everyone)\b/gi, "@\u200b$1");
const legacyMarker = (event: TicketEvent) => `[EFDS ticket:${event.ticket_id} event:${event.event_id}]`;
const hasEventBlock = (message: Message, event: TicketEvent) => {
  const prefix = `efds-ticket-event:${event.event_id}:`;
  return message.blocks?.some((block) => block.block_id?.startsWith(prefix)) ?? false;
};
const splitSections = (text: string, limit = 2800): string[] => {
  const sections: string[] = [];
  let current = "";
  for (let line of text.match(/[^\n]*\n|[^\n]+$/gu) ?? [text]) {
    while (line) {
      const lineChars = Array.from(line);
      const remaining = limit - Array.from(current).length;
      if (lineChars.length <= remaining) {
        current += line;
        line = "";
      } else if (current) {
        sections.push(current);
        current = "";
      } else {
        sections.push(lineChars.slice(0, limit).join(""));
        line = lineChars.slice(limit).join("");
      }
    }
  }
  if (current) sections.push(current);
  return sections.length ? sections : [text];
};
export function messageBlocks(text: string, eventId: string, preview = false): MessageBlock[] {
  const prefix = preview ? `efds-ticket-preview:${eventId}:${crypto.randomUUID()}` : `efds-ticket-event:${eventId}`;
  return splitSections(text).map((section, index) => ({
    type: "section",
    block_id: `${prefix}:${index}`,
    text: { type: "mrkdwn", text: section },
  }));
}
const assignees = (event: TicketEvent) => event.snapshot.assignees.map(safe).join(", ") || "Unassigned";
const label = (value: string) => status[value] ?? (value || "Open");

export function formatRoot(event: TicketEvent, websiteUrl: string): string {
  const base = websiteUrl.replace(/\/$/, "");
  const parsed = new URL(base);
  if (parsed.protocol !== "https:" || parsed.origin !== base || parsed.username || parsed.password ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(event.ticket_id)) {
    throw new Error("WEBSITE_URL must be a trusted HTTPS origin and ticket_id a UUID");
  }
  const due = event.snapshot.due_at ? String(event.snapshot.due_at).slice(0, 10) : "Not set";
  const description = safe(event.snapshot.description || "Not provided");
  return `${safe(event.snapshot.title)}\nStatus: ${safe(label(event.snapshot.execution_status))}\n` +
    `Assignees: ${assignees(event)}\nDue: ${safe(due)}\nDescription: ${description}\n` +
    `<${base}/dashboard/tickets/${event.ticket_id}|Open on website>`;
}
export function formatEvent(event: TicketEvent): string {
  return `${actions[event.action]}: ${safe(event.snapshot.title)}\n` +
    `Status: ${safe(label(event.snapshot.execution_status))} | Assignees: ${assignees(event)}`;
}

export async function runOnce(store: Store, slack: Slack, websiteUrl: string): Promise<string> {
  const event = await store.claim();
  if (!event) return "empty";
  const root = event.root_ts;
  let messages: Message[];
  try {
    messages = root ? await slack.replies(event.channel, root) : await slack.history(event.channel);
  } catch {
    await store.defer(event, event.reconcile_only);
    return event.reconcile_only ? "uncertain" : "retry";
  }
  const found = messages.find((msg) => (hasEventBlock(msg, event) || msg.text.includes(legacyMarker(event))) && msg.ts);
  if (found) {
    await store.finish(event, found.ts, root ?? found.ts);
    return "reconciled";
  }
  if (event.reconcile_only) {
    await store.defer(event, true);
    return "uncertain";
  }
  if (root) {
    try {
      if (!event.create_event_id) throw new Error("missing_root_event");
      await slack.update(event.channel, root,
        formatRoot({ ...event, event_id: event.create_event_id }, websiteUrl), event.event_id);
    } catch {
      await store.defer(event, false);
      return "retry";
    }
  }
  const text = root ? formatEvent(event) : formatRoot(event, websiteUrl);
  try {
    const ts = await slack.post(event.channel, text, root, event.event_id);
    await store.finish(event, ts, root ?? ts);
    return "posted";
  } catch (error) {
    // A failed acknowledgement is NOT a definite POST rejection. Never replay it.
    if (error instanceof SlackRejected) {
      await store.defer(event, false);
      return "retry";
    }
    await store.defer(event, true);
    return "uncertain";
  }
}
