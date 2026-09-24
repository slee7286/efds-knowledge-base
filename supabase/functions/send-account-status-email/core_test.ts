import { buildAccountNotice, deliverAccountNotice, type AccountNotice } from "./core.ts";
import { handler } from "./index.ts";
import type { Provider, SendResult } from "../send-auth-email/core.ts";

const assert = (ok: unknown, message = "assertion failed") => {
  if (!ok) throw new Error(message);
};
const base: AccountNotice = {
  id: "00000000-0000-4000-8000-000000000001",
  recipient_email: "student@example.org",
  previous_role: "member",
  new_role: "efds_member",
  previous_verification_status: "pending",
  new_verification_status: "approved",
  previous_officer_id: null,
  new_officer_id: null,
  previous_active: true,
  new_active: true,
};

Deno.test("account decision emails describe verification, standard membership, promotion, demotion, roster and deactivation", () => {
  const cases: Array<[Partial<AccountNotice>, string, string]> = [
    [{}, "membership has been verified", "EFDS membership is verified"],
    [{ new_role: "member", new_verification_status: "declined" }, "standard member access is confirmed", "access events and public resources"],
    [{ previous_role: "efds_member", new_role: "committee" }, "committee access", "committee access"],
    [{ previous_role: "committee", new_role: "admin" }, "administrator access", "administrator access"],
    [{ previous_role: "committee", new_role: "efds_member", previous_verification_status: "approved" }, "account access has changed", "verified EFDS member"],
    [{ previous_role: "committee", new_role: "committee", previous_verification_status: "approved", new_officer_id: "00000000-0000-4000-8000-000000000002" }, "committee identity", "roster identity"],
    [{ new_active: false }, "deactivated", "deactivated"],
  ];
  for (const [change, subject, body] of cases) {
    const mail = buildAccountNotice({ ...base, ...change });
    assert(mail.to === base.recipient_email);
    assert(mail.subject.includes(subject), mail.subject);
    assert(mail.text.includes(body), mail.text);
    assert(!mail.html.includes(base.id));
    assert(!mail.html.includes("pixel"));
  }
});

Deno.test("primary acceptance records one result; explicit rejection falls back to Brevo", async () => {
  for (const primaryOutcome of ["accepted", "rejected"] as const) {
    const providers: Provider[] = [];
    const results: SendResult[] = [];
    await deliverAccountNotice(
      base,
      async (_id, provider, result) => {
        providers.push(provider);
        results.push(result);
      },
      async (provider) => {
        providers.push(provider);
        return provider === "resend" && primaryOutcome === "rejected"
          ? { outcome: "rejected" }
          : { outcome: "accepted", messageId: "message-id" };
      },
    );
    assert(providers.join() === (primaryOutcome === "accepted" ? "resend,resend" : "resend,brevo,brevo"));
    assert(results[0].outcome === "accepted");
  }
});

Deno.test("uncertain primary does not switch providers", async () => {
  const providers: Provider[] = [];
  let result: SendResult | undefined;
  try {
    await deliverAccountNotice(
      base,
      async (_id, _provider, value) => { result = value; },
      async (provider) => {
        providers.push(provider);
        return { outcome: "uncertain" };
      },
    );
  } catch { /* expected */ }
  assert(providers.join() === "resend,resend");
  assert(result?.outcome === "uncertain");
});

Deno.test("worker rejects unauthenticated and non-POST requests before touching providers", async () => {
  assert((await handler(new Request("https://example.org", { method: "GET" }))).status === 405);
  assert((await handler(new Request("https://example.org", { method: "POST" }))).status === 401);
});
