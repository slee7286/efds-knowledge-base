import { deliver, type Ledger, type Mail, type Provider, type SendResult } from "../send-auth-email/core.ts";

export type TicketReminder = {
  id: string;
  ticket_id: string;
  recipient_email: string;
  ticket_title: string;
  source: "automatic" | "manual";
};

const SITE = "https://www.imperial-efds.com";
const escape = (value: string) => value.replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]!));

export function buildTicketReminder(row: TicketReminder): Mail {
  if (!/^[0-9a-f-]{36}$/.test(row.id) || !/^[0-9a-f-]{36}$/.test(row.ticket_id) ||
      typeof row.ticket_title !== "string" || row.ticket_title.length < 1 || row.ticket_title.length > 300 ||
      !["automatic", "manual"].includes(row.source) ||
      typeof row.recipient_email !== "string" ||
      !/^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$/.test(row.recipient_email)) {
    throw new Error("invalid_reminder");
  }
  const subject = "Reminder: your EFDS ticket needs attention";
  const detail = row.source === "automatic"
    ? "You were assigned a new EFDS committee ticket. Please review it and update its status as you make progress."
    : "A committee member has sent you a reminder about an assigned EFDS ticket.";
  const link = `${SITE}/dashboard/tickets/${row.ticket_id}`;
  return {
    to: row.recipient_email,
    subject,
    text: `${subject}\n\n${detail}\n\nTicket: ${row.ticket_title}\nOpen ticket: ${link}\n\nEFDS is an independent student society at Imperial College London.`,
    html: `<html><body style="font-family:Arial,sans-serif;color:#17233b;line-height:1.6"><h1 style="font-size:24px">${subject}</h1><p>${detail}</p><p><strong>${escape(row.ticket_title)}</strong></p><p><a href="${link}">Open the ticket in your EFDS workspace</a></p><p>EFDS is an independent student society at Imperial College London.</p></body></html>`,
    identity: `ticket-reminder:${row.id}`,
  };
}

export async function deliverTicketReminder(
  row: TicketReminder,
  finish: (id: string, provider: Provider, result: SendResult) => Promise<void>,
  send: (provider: Provider, mail: Mail, key: string) => Promise<SendResult>,
) {
  const ledger: Ledger = {
    async claim() { return "claimed"; },
    async finish(_key, provider, result) { await finish(row.id, provider, result); },
  };
  await deliver(buildTicketReminder(row), ledger, send);
}
