import { Webhook } from "standardwebhooks";
import { bearerMatches, parseProviderEvent } from "./core.ts";
import { handler } from "./index.ts";

const assert = (condition: unknown, label = "assertion failed") => {
  if (!condition) throw new Error(label);
};
const project = "https://example.supabase.co";

Deno.test("normalizes delivery events without retaining recipient data", () => {
  const resend = parseProviderEvent("resend", {
    type: "email.bounced",
    created_at: "2026-09-23T16:00:00Z",
    data: {
      email_id: "re-123",
      to: ["private@example.org"],
      subject: "secret",
    },
  });
  const brevo = parseProviderEvent("brevo", {
    event: "delivered",
    "message-id": "br-123",
    ts_event: 1790179200,
    email: "private@example.org",
  });
  assert(resend?.status === "bounced" && resend.messageId === "re-123");
  assert(brevo?.status === "delivered" && brevo.messageId === "br-123");
  assert(!JSON.stringify(resend).includes("private@example.org"));
  assert(!JSON.stringify(brevo).includes("private@example.org"));
  assert(
    parseProviderEvent("resend", { type: "email.opened", data: {} }) === null,
  );
});

Deno.test("Brevo bearer token requires an exact match", async () => {
  assert(await bearerMatches("Bearer secret", "secret"));
  assert(!await bearerMatches("Bearer secretx", "secret"));
  assert(!await bearerMatches("Basic secret", "secret"));
});

Deno.test("Resend forged requests are rejected before database access", async () => {
  Deno.env.set(
    "RESEND_WEBHOOK_SECRET",
    `whsec_${btoa("test-secret-at-least-32-bytes-long!!!")}`,
  );
  Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "test-service-key");
  Deno.env.set("SUPABASE_URL", project);
  const original = globalThis.fetch;
  let called = false;
  globalThis.fetch = () => {
    called = true;
    throw new Error("unexpected fetch");
  };
  try {
    const response = await handler(
      new Request(`${project}/functions/v1/auth-email-events/resend`, {
        method: "POST",
        body: JSON.stringify({ type: "email.delivered" }),
      }),
    );
    assert(response.status === 401 && !called);
  } finally {
    globalThis.fetch = original;
  }
});

Deno.test("signed Resend and authenticated Brevo callbacks write normalized events", async () => {
  const rawSecret = btoa("test-secret-at-least-32-bytes-long!!!");
  Deno.env.set("RESEND_WEBHOOK_SECRET", `whsec_${rawSecret}`);
  Deno.env.set("BREVO_WEBHOOK_TOKEN", "test-brevo-token");
  Deno.env.set("SUPABASE_SERVICE_ROLE_KEY", "test-service-key");
  Deno.env.set("SUPABASE_URL", project);
  const stamp = new Date();
  const resendBody = JSON.stringify({
    type: "email.delivered",
    created_at: stamp.toISOString(),
    data: { email_id: "re-123", to: ["private@example.org"] },
  });
  const signed = new Webhook(rawSecret).sign("event-123", stamp, resendBody);
  const realFetch = globalThis.fetch;
  const requests: Record<string, unknown>[] = [];
  globalThis.fetch = (_input, init) => {
    requests.push(JSON.parse(String(init?.body)));
    return Promise.resolve(new Response("true", { status: 200 }));
  };
  try {
    const resend = await handler(
      new Request(`${project}/functions/v1/auth-email-events/resend`, {
        method: "POST",
        body: resendBody,
        headers: {
          "svix-id": "event-123",
          "svix-timestamp": String(Math.floor(stamp.getTime() / 1000)),
          "svix-signature": signed,
        },
      }),
    );
    const brevo = await handler(
      new Request(`${project}/functions/v1/auth-email-events/brevo`, {
        method: "POST",
        body: JSON.stringify({
          event: "hard_bounce",
          "message-id": "br-123",
          ts_event: Math.floor(stamp.getTime() / 1000),
          email: "private@example.org",
        }),
        headers: { authorization: "Bearer test-brevo-token" },
      }),
    );
    assert(resend.status === 200 && brevo.status === 200);
    assert(requests.length === 2);
    assert(
      requests[0].p_status === "delivered" &&
        requests[1].p_status === "bounced",
    );
    assert(!JSON.stringify(requests).includes("private@example.org"));
  } finally {
    globalThis.fetch = realFetch;
  }
});
