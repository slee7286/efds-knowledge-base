export type EmailProvider = "resend" | "brevo";
export type DeliveryState =
  | "delivered"
  | "deferred"
  | "bounced"
  | "complained"
  | "blocked";
export type DeliveryEvent = {
  provider: EmailProvider;
  messageId: string;
  status: DeliveryState;
  occurredAt: string;
};

function safeMessageId(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= 256
    ? value
    : null;
}

export function parseProviderEvent(
  provider: EmailProvider,
  payload: Record<string, unknown>,
): DeliveryEvent | null {
  let messageId: string | null;
  let status: DeliveryState | undefined;
  let timestamp: unknown;
  if (provider === "resend") {
    const data = payload.data;
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    messageId = safeMessageId((data as Record<string, unknown>).email_id);
    timestamp = payload.created_at;
    const statuses: Record<string, DeliveryState> = {
      "email.delivered": "delivered",
      "email.delivery_delayed": "deferred",
      "email.bounced": "bounced",
      "email.complained": "complained",
      "email.failed": "blocked",
      "email.suppressed": "blocked",
    };
    status = statuses[String(payload.type)];
  } else {
    messageId = safeMessageId(payload["message-id"]);
    timestamp = payload.ts_event ?? payload.ts;
    const statuses: Record<string, DeliveryState> = {
      delivered: "delivered",
      deferred: "deferred",
      soft_bounce: "deferred",
      hard_bounce: "bounced",
      spam: "complained",
      blocked: "blocked",
      invalid_email: "blocked",
      error: "blocked",
    };
    status = statuses[String(payload.event)];
  }
  if (!status) return null;
  if (!messageId) throw new Error("invalid_provider_event");
  const when = provider === "brevo" && typeof timestamp === "number"
    ? new Date(timestamp * 1000)
    : new Date(String(timestamp));
  if (Number.isNaN(when.getTime())) throw new Error("invalid_provider_event");
  return { provider, messageId, status, occurredAt: when.toISOString() };
}

export async function bearerMatches(
  header: string | null,
  token: string,
): Promise<boolean> {
  const supplied = header?.startsWith("Bearer ") ? header.slice(7) : "";
  if (!supplied || !token) return false;
  const digest = async (input: string) =>
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input)),
    );
  const [actual, expected] = await Promise.all([
    digest(supplied),
    digest(token),
  ]);
  let difference = 0;
  for (let i = 0; i < expected.length; i++) {
    difference |= actual[i] ^ expected[i];
  }
  return difference === 0;
}
