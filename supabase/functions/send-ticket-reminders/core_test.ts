import { buildTicketReminder, deliverTicketReminder, type TicketReminder } from "./core.ts";
import { handler } from "./index.ts";
import type { Provider } from "../send-auth-email/core.ts";

const assert = (condition: unknown, message = "assertion failed") => {
  if (!condition) throw new Error(message);
};
const base: TicketReminder = {
  id: "00000000-0000-4000-8000-000000000001",
  ticket_id: "00000000-0000-4000-8000-000000000002",
  recipient_email: "officer@example.org",
  ticket_title: "Confirm <venue> & speaker",
  source: "automatic",
};

Deno.test("reminder mail links to the ticket and escapes its title", () => {
  const mail = buildTicketReminder(base);
  assert(mail.to === base.recipient_email);
  assert(mail.text.includes(base.ticket_title));
  assert(mail.html.includes("Confirm &lt;venue&gt; &amp; speaker"));
  assert(!mail.html.includes(base.ticket_title));
  assert(mail.text.includes(`/dashboard/tickets/${base.ticket_id}`));
  assert(buildTicketReminder({ ...base, source: "manual" }).text.includes("sent you a reminder"));
});

Deno.test("primary acceptance and explicit rejection use the existing fallback", async () => {
  for (const primary of ["accepted", "rejected"] as const) {
    const providers: Provider[] = [];
    let finished = false;
    await deliverTicketReminder(
      base,
      async (_id, provider, result) => {
        assert(provider === (primary === "accepted" ? "resend" : "brevo"));
        assert(result.outcome === "accepted");
        finished = true;
      },
      async (provider) => {
        providers.push(provider);
        return provider === "resend" && primary === "rejected"
          ? { outcome: "rejected" }
          : { outcome: "accepted", messageId: "provider-id" };
      },
    );
    assert(finished);
    assert(providers.join() === (primary === "accepted" ? "resend" : "resend,brevo"));
  }
});

Deno.test("unverified worker requests cannot claim reminders", async () => {
  assert((await handler(new Request("https://example.org", { method: "GET" }))).status === 405);
  assert((await handler(new Request("https://example.org", { method: "POST" }))).status === 401);
});
