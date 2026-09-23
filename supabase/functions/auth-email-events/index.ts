import { Webhook } from "standardwebhooks";
import {
  bearerMatches,
  type EmailProvider,
  parseProviderEvent,
} from "./core.ts";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });

export async function handler(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const provider: EmailProvider | null = url.pathname.endsWith("/resend")
    ? "resend"
    : url.pathname.endsWith("/brevo")
    ? "brevo"
    : null;
  if (!provider) return json({ error: "Unknown provider" }, 404);
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);
  const secretName = provider === "resend"
    ? "RESEND_WEBHOOK_SECRET"
    : "BREVO_WEBHOOK_TOKEN";
  const secret = Deno.env.get(secretName);
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  if (!secret || !serviceKey || !supabaseUrl) {
    return json({ error: "Not configured" }, 503);
  }
  if (Number(req.headers.get("content-length") ?? 0) > 32768) {
    return json({ error: "Payload too large" }, 413);
  }
  const raw = await req.text();
  if (raw.length > 32768) return json({ error: "Payload too large" }, 413);
  let payload: Record<string, unknown>;
  try {
    if (provider === "resend") {
      payload = new Webhook(secret.replace(/^whsec_/, "")).verify(raw, {
        "webhook-id": req.headers.get("svix-id") ?? "",
        "webhook-timestamp": req.headers.get("svix-timestamp") ?? "",
        "webhook-signature": req.headers.get("svix-signature") ?? "",
      }) as Record<string, unknown>;
    } else {
      if (!await bearerMatches(req.headers.get("authorization"), secret)) {
        return json({ error: "Unauthorized" }, 401);
      }
      payload = JSON.parse(raw) as Record<string, unknown>;
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return json({ error: "Invalid event" }, 400);
    }
  } catch {
    return json({ error: "Invalid event or signature" }, 401);
  }
  let event;
  try {
    event = parseProviderEvent(provider, payload);
  } catch {
    return json({ error: "Invalid event" }, 400);
  }
  if (!event) return json({ ignored: true });
  try {
    const result = await fetch(
      `${supabaseUrl}/rest/v1/rpc/record_auth_email_event`,
      {
        method: "POST",
        headers: {
          apikey: serviceKey,
          Authorization: `Bearer ${serviceKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          p_provider: event.provider,
          p_message_id: event.messageId,
          p_status: event.status,
          p_occurred_at: event.occurredAt,
        }),
        signal: AbortSignal.timeout(3000),
      },
    );
    if (!result.ok) return json({ error: "Event storage unavailable" }, 503);
    const matched = await result.json();
    // A webhook may include unrelated mail from the same provider account.
    // Never retain recipient details or retry an unmatched event indefinitely.
    return json({ recorded: matched === true });
  } catch {
    return json({ error: "Event storage unavailable" }, 503);
  }
}

if (import.meta.main) Deno.serve(handler);
