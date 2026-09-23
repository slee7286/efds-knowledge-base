import { Webhook } from "standardwebhooks";
import {
  buildMails,
  classifyResponse,
  deliver,
  type Ledger,
  type Payload,
  providerRequest,
} from "./core.ts";

const REQUIRED = [
  "BREVO_API_KEY",
  "RESEND_API_KEY",
  "SEND_EMAIL_HOOK_SECRET",
  "SUPABASE_URL",
  "SUPABASE_SERVICE_ROLE_KEY",
];
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
export async function handler(req: Request): Promise<Response> {
  const missing = REQUIRED.filter((name) => !Deno.env.get(name));
  // Configuration presence only: never return secret values or provider responses.
  if (req.method === "GET") {
    return json({
      configured: !missing.length,
      missing,
      sender: "no-reply@imperial-efds.com",
      active:
        "Check Authentication > Hooks; deployment alone does not activate this function.",
    });
  }
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);
  if (missing.length) {
    return json({
      error: {
        http_code: 503,
        message: "Authentication email delivery is not configured.",
      },
    }, 503);
  }
  if (Number(req.headers.get("content-length") ?? 0) > 65536) {
    return json({ error: "Payload too large" }, 413);
  }
  const raw = await req.text();
  if (raw.length > 65536) return json({ error: "Payload too large" }, 413);
  let payload: Payload;
  try {
    const secret = Deno.env.get("SEND_EMAIL_HOOK_SECRET")!.replace(
      /^v1,whsec_/,
      "",
    );
    payload = new Webhook(secret).verify(
      raw,
      Object.fromEntries(req.headers),
    ) as Payload;
  } catch {
    return json({ error: "Invalid webhook signature" }, 401);
  }
  const deadline = Date.now() + 4200;
  const timedFetch = (url: string, init: RequestInit, timeout: number) => {
    const remaining = Math.min(timeout, deadline - Date.now());
    if (remaining <= 0) throw new Error("delivery_deadline");
    return fetch(url, { ...init, signal: AbortSignal.timeout(remaining) });
  };
  const url = Deno.env.get("SUPABASE_URL")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const headers = {
    apikey: serviceKey,
    Authorization: `Bearer ${serviceKey}`,
    "Content-Type": "application/json",
  };
  const endpoint = `${url}/rest/v1/auth_email_deliveries`;
  const ledger: Ledger = {
    async claim(key) {
      const response = await timedFetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({ delivery_key: key, status: "sending" }),
      }, 700);
      if (response.ok) {
        await response.body?.cancel();
        return "claimed";
      }
      if (response.status !== 409) {
        await response.body?.cancel();
        throw new Error("delivery_ledger_unavailable");
      }
      await response.body?.cancel();
      const prior = await timedFetch(
        `${endpoint}?delivery_key=eq.${key}&select=status`,
        { headers },
        700,
      );
      if (!prior.ok) throw new Error("delivery_ledger_unavailable");
      const rows = await prior.json();
      return rows[0]?.status === "accepted" ? "accepted" : "blocked";
    },
    async finish(key, provider, status) {
      const result = await timedFetch(`${endpoint}?delivery_key=eq.${key}`, {
        method: "PATCH",
        headers,
        body: JSON.stringify({
          provider,
          status,
          updated_at: new Date().toISOString(),
        }),
      }, 700);
      if (!result.ok) throw new Error("delivery_ledger_update_failed");
      await result.body?.cancel();
      console.info(
        JSON.stringify({ event: "auth_email_attempt", provider, status }),
      );
    },
  };
  try {
    const mails = buildMails(payload, url, req.headers.get("webhook-id")!);
    await Promise.all(
      mails.map((mail) =>
        deliver(mail, ledger, async (provider, item, key) => {
          const [target, init] = providerRequest(
            provider,
            item,
            key,
            Deno.env.get(
              provider === "resend" ? "RESEND_API_KEY" : "BREVO_API_KEY",
            )!,
          );
          const response = await timedFetch(target, init, 1300);
          const body = await response.json().catch(() => ({}));
          return classifyResponse(response.status, body, provider);
        })
      ),
    );
    return json({});
  } catch {
    // Do not log errors, payloads, links, tokens, recipients or raw provider responses.
    console.error("auth_email_delivery_failed");
    return json({
      error: {
        http_code: 503,
        message:
          "We could not confirm email delivery. Please wait before requesting a fresh link.",
      },
    }, 503);
  }
}
if (import.meta.main) Deno.serve(handler);
