import { providerRequest, providerResult } from "../send-auth-email/core.ts";
import {
  deliverAccountNotice,
  type AccountNotice,
} from "./core.ts";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });

export async function handler(req: Request): Promise<Response> {
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);
  const token = req.headers.get("X-EFDS-Worker-Token") ?? "";
  if (!/^[0-9a-f]{64}$/.test(token)) return json({ error: "Unauthorized" }, 401);
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  const resendKey = Deno.env.get("RESEND_API_KEY");
  const brevoKey = Deno.env.get("BREVO_API_KEY");
  if (!url || !key || !resendKey || !brevoKey) {
    return json({ error: "Mail worker is not configured" }, 503);
  }
  const headers = {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
  };
  const rpc = async (name: string, body: unknown) => {
    const response = await fetch(`${url}/rest/v1/rpc/${name}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(6000),
    });
    if (!response.ok) throw new Error(`database_${name}_failed`);
    return response.json();
  };
  try {
    if (await rpc("verify_account_notice_worker", { p_token: token }) !== true) {
      return json({ error: "Unauthorized" }, 401);
    }
    const rows = await rpc("claim_account_status_notices", { p_limit: 5 });
    if (!Array.isArray(rows)) throw new Error("invalid_outbox_response");
    let accepted = 0;
    let attention = 0;
    for (const row of rows as AccountNotice[]) {
      try {
        await deliverAccountNotice(
          row,
          async (id, provider, result) => {
            await rpc("finish_account_status_notice", {
              p_id: id,
              p_state: result.outcome,
              p_provider: provider,
              p_message_id: result.messageId ?? null,
            });
          },
          async (provider, mail, identity) => {
            const [target, init] = providerRequest(
              provider,
              mail,
              identity,
              provider === "resend" ? resendKey : brevoKey,
            );
            const response = await fetch(target, {
              ...init,
              signal: AbortSignal.timeout(6500),
            });
            const body = await response.json().catch(() => ({}));
            return providerResult(response.status, body, provider);
          },
        );
        accepted++;
      } catch {
        // The outbox preserves rejected/uncertain results. A crashed delivery
        // remains 'sending' for admin investigation rather than risking replay.
        attention++;
      }
    }
    console.info(JSON.stringify({ event: "account_notice_batch", accepted, attention }));
    return json({ claimed: rows.length, accepted, attention });
  } catch {
    console.error("account_notice_worker_failed");
    return json({ error: "Mail worker could not process the queue" }, 503);
  }
}

if (import.meta.main) Deno.serve(handler);
