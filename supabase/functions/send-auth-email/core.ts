export type Mail = {
  to: string;
  subject: string;
  html: string;
  text: string;
  identity: string;
};
export type Provider = "resend" | "brevo";
export type Outcome = "accepted" | "rejected" | "uncertain";
export type SendResult = {
  outcome: Outcome;
  messageId?: string;
  quotaOrRateLimited?: boolean;
};
export interface Ledger {
  claim(key: string): Promise<"claimed" | "accepted" | "blocked">;
  finish(
    key: string,
    provider: Provider,
    result: SendResult,
    resendRejected: boolean,
    resendQuotaOrRateLimited: boolean,
  ): Promise<void>;
}
export type Payload = {
  user: { email: string; new_email?: string };
  email_data: {
    email_action_type: string;
    token?: string;
    token_hash?: string;
    token_new?: string;
    token_hash_new?: string;
    redirect_to?: string;
  };
};
const SITE = "https://www.imperial-efds.com";
export const SENDER = "no-reply@imperial-efds.com";
const escape = (s: string) =>
  s.replace(
    /[&<>"']/g,
    (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]!),
  );
function address(value: unknown): string {
  if (
    typeof value !== "string" || value.length > 254 ||
    !/^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$/.test(value)
  ) throw new Error("invalid_recipient");
  return value;
}
function redirectFor(action: string, raw?: string) {
  const fallback = action === "recovery"
    ? `${SITE}/auth/recovery?flow=reset`
    : `${SITE}/auth/callback`;
  if (!raw) return fallback;
  const url = new URL(raw);
  if (
    url.origin !== SITE || url.username || url.password ||
    !["/auth/callback", "/auth/recovery"].includes(url.pathname)
  ) throw new Error("invalid_redirect");
  if (action === "recovery" && url.pathname !== "/auth/recovery") {
    return fallback;
  }
  // Only retain our existing setup/reset distinction. Never forward arbitrary next URLs.
  return url.pathname === "/auth/recovery"
    ? `${SITE}/auth/recovery?flow=${
      url.searchParams.get("flow") === "setup" ? "setup" : "reset"
    }`
    : `${SITE}/auth/callback`;
}
export function buildMails(
  payload: Payload,
  supabaseUrl: string,
  eventId: string,
): Mail[] {
  const { user, email_data: data } = payload;
  if (!user || !data) throw new Error("invalid_payload");
  const action = data.email_action_type;
  const titles: Record<string, string> = {
    signup: "Confirm your EFDS account",
    invite: "Your EFDS invitation",
    magiclink: "Sign in to EFDS",
    recovery: "Set your EFDS password",
    email_change: "Confirm your email change",
    reauthentication: "Confirm your EFDS identity",
  };
  const notifications = [
    "password_changed_notification",
    "email_changed_notification",
    "phone_changed_notification",
    "identity_linked_notification",
    "identity_unlinked_notification",
    "mfa_factor_enrolled_notification",
    "mfa_factor_unenrolled_notification",
  ];
  if (notifications.includes(action)) {
    const subject = "Your EFDS account security settings changed";
    const text =
      "A security setting on your EFDS account was changed. If you did not make this change, contact the EFDS administrator and secure your account immediately.";
    return [{
      to: address(user.email),
      subject,
      text,
      html: `<p>${text}</p>`,
      identity: eventId,
    }];
  }
  if (!titles[action]) throw new Error("unsupported_email_action");
  const make = (to: string, hash?: string, token?: string): Mail => {
    if (action === "reauthentication") {
      if (!token || !/^\d{6,10}$/.test(token)) throw new Error("missing_token");
      const text =
        `Your EFDS confirmation code is ${token}. If you did not request this, ignore this email.`;
      return {
        to: address(to),
        subject: titles[action],
        text,
        html: `<p>${text}</p>`,
        identity: `${action}:${token}:${eventId}`,
      };
    }
    if (!hash || !/^[a-zA-Z0-9_-]{16,256}$/.test(hash)) {
      throw new Error("missing_token_hash");
    }
    // The site's confirmation page requires a human POST before consuming the
    // one-time token. Mail scanners that open a link cannot use it up.
    const link = action === "email_change"
      ? new URL("/auth/v1/verify", supabaseUrl)
      : new URL("/auth/confirm", SITE);
    if (action === "email_change") {
      link.searchParams.set("token", hash);
      link.searchParams.set("type", action);
      link.searchParams.set(
        "redirect_to",
        redirectFor(action, data.redirect_to),
      );
    } else {
      link.searchParams.set("token_hash", hash);
      link.searchParams.set(
        "type",
        action === "recovery" ? "recovery" : "email",
      );
      if (action !== "recovery") {
        link.searchParams.set("next", redirectFor(action, data.redirect_to));
      }
    }
    const text = `${
      titles[action]
    }\n\nContinue: ${link}\n\nUse the browser where you started. If you did not request this, ignore this email.\n\nEFDS is a student society at Imperial College London.`;
    const html =
      `<html><body style="font-family:Arial,sans-serif;color:#17233b;line-height:1.6"><h1 style="font-size:24px">${
        titles[action]
      }</h1><p><a href="${
        escape(link.toString())
      }">Continue securely</a></p><p>Use the browser where you started. If you did not request this, ignore this email.</p><p>EFDS is a student society at Imperial College London.</p></body></html>`;
    return {
      to: address(to),
      subject: titles[action],
      text,
      html,
      identity: `${action}:${hash}`,
    };
  };
  if (action === "email_change") {
    if (!user.new_email) throw new Error("missing_new_email");
    const mails = [
      make(user.new_email, data.token_hash, data.token_new || data.token),
    ];
    if (data.token_hash_new) {
      mails.unshift(make(user.email, data.token_hash_new, data.token));
    }
    return mails;
  }
  return [make(user.email, data.token_hash, data.token)];
}
export async function deliveryKey(mail: Mail): Promise<string> {
  const bytes = new TextEncoder().encode(
    `${mail.to.toLowerCase()}:${mail.identity}`,
  );
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
}
export async function deliver(
  mail: Mail,
  ledger: Ledger,
  send: (provider: Provider, mail: Mail, key: string) => Promise<SendResult>,
): Promise<void> {
  const key = await deliveryKey(mail);
  const state = await ledger.claim(key);
  if (state === "accepted") return;
  if (state !== "claimed") throw new Error("previous_delivery_not_confirmed");
  let provider: Provider = "resend";
  let result: SendResult;
  try {
    result = await send(provider, mail, key);
  } catch {
    result = { outcome: "uncertain" };
  }
  // Resend remembers this idempotency key for 24 hours. A single same-provider
  // retry can resolve a timeout/5xx without risking a second email.
  if (result.outcome === "uncertain") {
    try {
      result = await send(provider, mail, key);
    } catch {
      result = { outcome: "uncertain" };
    }
    // The first attempt may have been accepted even if this one was rejected.
    if (result.outcome === "rejected") result = { outcome: "uncertain" };
  }
  const resendRejected = result.outcome === "rejected";
  const resendQuotaOrRateLimited = resendRejected &&
    Boolean(result.quotaOrRateLimited);
  // Only explicit rejection proves the primary did not accept the message.
  // Timeouts/5xx can have ambiguous outcomes: do not send a second copy.
  if (resendRejected) {
    provider = "brevo";
    try {
      result = await send(provider, mail, key);
    } catch {
      result = { outcome: "uncertain" };
    }
  }
  await ledger.finish(
    key,
    provider,
    result,
    resendRejected,
    resendQuotaOrRateLimited,
  );
  if (result.outcome !== "accepted") throw new Error(`email_${result.outcome}`);
}
export function providerRequest(
  provider: Provider,
  mail: Mail,
  key: string,
  apiKey: string,
): [string, RequestInit] {
  if (provider === "resend") {
    return ["https://api.resend.com/emails", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
        "Idempotency-Key": key,
      },
      body: JSON.stringify({
        from: `EFDS <${SENDER}>`,
        to: [mail.to],
        subject: mail.subject,
        html: mail.html,
        text: mail.text,
      }),
    }];
  }
  return ["https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: { "Content-Type": "application/json", "api-key": apiKey },
    body: JSON.stringify({
      sender: { name: "EFDS", email: SENDER },
      to: [{ email: mail.to }],
      subject: mail.subject,
      htmlContent: mail.html,
      textContent: mail.text,
    }),
  }];
}
export function classifyResponse(
  status: number,
  body: Record<string, unknown>,
  provider: Provider,
): Outcome {
  if (status >= 200 && status < 300) {
    const id = body[provider === "resend" ? "id" : "messageId"];
    return typeof id === "string" && id.length > 0 && id.length <= 256
      ? "accepted"
      : "uncertain";
  }
  // 409 may be a concurrent idempotent send; never switch providers on it.
  if ([400, 401, 402, 403, 404, 422, 429].includes(status)) return "rejected";
  return "uncertain";
}

export function providerResult(
  status: number,
  body: Record<string, unknown>,
  provider: Provider,
): SendResult {
  const outcome = classifyResponse(status, body, provider);
  const id = body[provider === "resend" ? "id" : "messageId"];
  return {
    outcome,
    ...(outcome === "accepted" && typeof id === "string" && id.length <= 256
      ? { messageId: id }
      : {}),
    quotaOrRateLimited: status === 429,
  };
}
