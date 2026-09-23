// deno-lint-ignore-file require-await
// Async test doubles preserve the provider/ledger Promise contract without I/O.
import {
  buildMails,
  classifyResponse,
  deliver,
  deliveryKey,
  type Ledger,
  type Outcome,
  type Provider,
  providerRequest,
} from "./core.ts";
import { handler } from "./index.ts";
import { Webhook } from "standardwebhooks";
const assert = (condition: unknown, label = "assertion failed") => {
  if (!condition) throw new Error(label);
};
const project = "https://immldithmugfrpojetmm.supabase.co";
const sample = (action = "magiclink") => ({
  user: { email: "member@example.org" },
  email_data: {
    email_action_type: action,
    token_hash: "a".repeat(64),
    redirect_to: "https://www.imperial-efds.com/auth/callback",
  },
});
function memoryLedger(): Ledger {
  const records = new Map<string, string>();
  return {
    async claim(key) {
      const prior = records.get(key);
      if (prior) return prior === "accepted" ? "accepted" : "blocked";
      records.set(key, "sending");
      return "claimed";
    },
    async finish(key, _provider, outcome) {
      records.set(key, outcome);
    },
  };
}
Deno.test("email links wait for the human confirmation POST", () => {
  for (const action of ["signup", "invite", "magiclink", "recovery"]) {
    const data = sample(action);
    if (action === "recovery") {
      data.email_data.redirect_to =
        "https://www.imperial-efds.com/auth/recovery?flow=setup";
    }
    const mail = buildMails(data, project, "event")[0];
    const url = new URL(mail.text.split("Continue: ")[1].split("\n")[0]);
    assert(
      url.origin === "https://www.imperial-efds.com" &&
        url.pathname === "/auth/confirm",
    );
    assert(
      url.searchParams.get("type") ===
        (action === "recovery" ? "recovery" : "email"),
    );
    assert(url.searchParams.get("token_hash") === "a".repeat(64));
    if (action === "recovery") assert(url.searchParams.get("next") === null);
  }
  const setup = sample("magiclink");
  setup.email_data.redirect_to =
    "https://www.imperial-efds.com/auth/recovery?flow=setup";
  const setupLink = new URL(
    buildMails(setup, project, "event")[0].text.split("Continue: ")[1].split(
      "\n",
    )[0],
  );
  assert(
    setupLink.searchParams.get("next") ===
      "https://www.imperial-efds.com/auth/recovery?flow=setup",
  );
});
Deno.test("rejects external redirects and unsupported email actions", () => {
  for (
    const payload of [{
      ...sample(),
      email_data: {
        ...sample().email_data,
        redirect_to: "https://attacker.example/auth/callback",
      },
    }, sample("unknown")]
  ) {
    let threw = false;
    try {
      buildMails(payload, project, "event");
    } catch {
      threw = true;
    }
    assert(threw);
  }
});
Deno.test("secure email change sends the documented hash to each address", () => {
  const payload = {
    user: { email: "old@example.org", new_email: "new@example.org" },
    email_data: {
      ...sample("email_change").email_data,
      token_hash_new: "b".repeat(64),
    },
  };
  const mails = buildMails(payload, project, "event");
  assert(mails.length === 2);
  assert(
    mails[0].to === "old@example.org" && mails[0].text.includes("b".repeat(64)),
  );
  assert(
    mails[1].to === "new@example.org" && mails[1].text.includes("a".repeat(64)),
  );
});
Deno.test("reauthentication and security notification do not create broken links", () => {
  const p = {
    ...sample("reauthentication"),
    email_data: { ...sample("reauthentication").email_data, token: "123456" },
  };
  assert(buildMails(p, project, "event")[0].text.includes("123456"));
  assert(
    !buildMails(sample("password_changed_notification"), project, "event")[0]
      .text.includes("/verify"),
  );
});
Deno.test("accepted primary is not sent again, including concurrent/replayed requests", async () => {
  const ledger = memoryLedger();
  const calls: Provider[] = [];
  const mail = buildMails(sample(), project, "event")[0];
  const send = async (p: Provider): Promise<Outcome> => {
    calls.push(p);
    return "accepted";
  };
  await deliver(mail, ledger, send);
  await deliver(mail, ledger, send);
  assert(calls.join() === "resend");
});
Deno.test("explicit primary rejection uses backup once and preserves acceptance on retry", async () => {
  const ledger = memoryLedger();
  const calls: Provider[] = [];
  const mail = buildMails(sample(), project, "event")[0];
  const send = async (p: Provider): Promise<Outcome> => {
    calls.push(p);
    return p === "resend" ? "rejected" : "accepted";
  };
  await deliver(mail, ledger, send);
  await deliver(mail, ledger, send);
  assert(calls.join() === "resend,brevo");
});
Deno.test("uncertain primary, provider exception, and ledger failure never invoke backup", async () => {
  const mail = buildMails(sample(), project, "event")[0];
  for (const throws of [false, true]) {
    const calls: Provider[] = [];
    let failed = false;
    try {
      await deliver(mail, memoryLedger(), async (p) => {
        calls.push(p);
        if (throws) throw new Error("timeout");
        return "uncertain";
      });
    } catch {
      failed = true;
    }
    assert(failed && calls.join() === "resend");
  }
  let called = false;
  const ledger = memoryLedger();
  ledger.claim = () => {
    throw new Error("offline");
  };
  try {
    await deliver(mail, ledger, async () => {
      called = true;
      return "accepted";
    });
  } catch { /* expected */ }
  assert(!called);
});
Deno.test("two rejected providers return failure without replaying rejected attempts", async () => {
  const mail = buildMails(sample(), project, "event")[0];
  const ledger = memoryLedger();
  let calls = 0;
  for (let i = 0; i < 2; i++) {
    let failed = false;
    try {
      await deliver(mail, ledger, async () => {
        calls++;
        return "rejected";
      });
    } catch {
      failed = true;
    }
    assert(failed);
  }
  assert(calls === 2);
});
Deno.test("provider payloads preserve recipient and content without tracking flags", async () => {
  const mail = buildMails(sample(), project, "event")[0];
  const key = await deliveryKey(mail);
  assert(key.length === 64 && !key.includes(mail.to));
  for (const p of ["resend", "brevo"] as const) {
    const [url, init] = providerRequest(p, mail, key, "fake-test-key");
    const body = JSON.parse(String(init.body));
    assert(url.startsWith("https://api."));
    assert((p === "resend" ? body.to[0] : body.to[0].email) === mail.to);
    assert((p === "resend" ? body.html : body.htmlContent) === mail.html);
  }
  assert(classifyResponse(429, {}, "resend") === "rejected");
  assert(classifyResponse(503, {}, "resend") === "uncertain");
  assert(classifyResponse(409, {}, "resend") === "uncertain");
  assert(classifyResponse(200, {}, "resend") === "uncertain");
  assert(classifyResponse(201, { messageId: "id" }, "brevo") === "accepted");
});
Deno.test("HTTP rejects forged webhook and exposes only configuration presence", async () => {
  for (
    const key of [
      "BREVO_API_KEY",
      "RESEND_API_KEY",
      "SUPABASE_SERVICE_ROLE_KEY",
    ]
  ) Deno.env.set(key, "test-secret");
  Deno.env.set("SUPABASE_URL", project);
  Deno.env.set(
    "SEND_EMAIL_HOOK_SECRET",
    btoa("test-key-at-least-32-bytes-long!!!"),
  );
  const response = await handler(
    new Request("https://example.test", {
      method: "POST",
      body: JSON.stringify(sample()),
    }),
  );
  assert(response.status === 401);
  const health = await (await handler(new Request("https://example.test")))
    .text();
  assert(
    !health.includes("test-secret") && health.includes('"configured":true'),
  );
});
Deno.test("signed end-to-end hook falls back and never logs email content", async () => {
  const secret = btoa("test-key-at-least-32-bytes-long!!!");
  Deno.env.set("SEND_EMAIL_HOOK_SECRET", secret);
  const payload = JSON.stringify(sample());
  const stamp = new Date();
  const id = "evt-test";
  const signature = new Webhook(secret).sign(id, stamp, payload);
  const realFetch = globalThis.fetch;
  const logs: string[] = [];
  const realInfo = console.info;
  const targets: string[] = [];
  console.info = (...args) => logs.push(args.join(" "));
  globalThis.fetch = (input, init) => {
    const url = String(input);
    targets.push(url);
    if (url.includes("/rest/")) {
      return Promise.resolve(new Response(null, { status: 201 }));
    }
    if (url.includes("resend")) {
      return Promise.resolve(
        new Response(JSON.stringify({ name: "daily_quota_exceeded" }), {
          status: 429,
        }),
      );
    }
    assert(init?.method === "POST");
    return Promise.resolve(
      new Response(JSON.stringify({ messageId: "test" }), { status: 201 }),
    );
  };
  try {
    const response = await handler(
      new Request("https://example.test", {
        method: "POST",
        body: payload,
        headers: {
          "webhook-id": id,
          "webhook-timestamp": String(Math.floor(stamp.getTime() / 1000)),
          "webhook-signature": signature,
        },
      }),
    );
    assert(response.status === 200);
    assert(targets.some((t) => t.includes("brevo")));
    assert(
      logs.every((l) =>
        !l.includes("member@example.org") && !l.includes("a".repeat(64))
      ),
    );
  } finally {
    globalThis.fetch = realFetch;
    console.info = realInfo;
  }
});
