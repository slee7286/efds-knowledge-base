import {
  deliver,
  type Ledger,
  type Mail,
  type Provider,
  type SendResult,
} from "../send-auth-email/core.ts";

export type AccountNotice = {
  id: string;
  recipient_email: string;
  previous_role: string;
  new_role: string;
  previous_verification_status: string;
  new_verification_status: string;
  previous_officer_id: string | null;
  new_officer_id: string | null;
  previous_active: boolean;
  new_active: boolean;
};

const SITE = "https://www.imperial-efds.com";
const roleName: Record<string, string> = {
  viewer: "viewer",
  member: "member",
  efds_member: "verified EFDS member",
  committee: "committee member",
  admin: "administrator",
};
const emailAddress = (value: string) => {
  if (
    typeof value !== "string" || value.length > 254 ||
    !/^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$/.test(value)
  ) throw new Error("invalid_recipient");
  return value;
};

export function buildAccountNotice(row: AccountNotice): Mail {
  if (!/^[0-9a-f-]{36}$/.test(row.id) || !roleName[row.new_role]) {
    throw new Error("invalid_notice");
  }
  let subject = "Your EFDS account access has changed";
  let detail = `Your EFDS account is now a ${roleName[row.new_role]}.`;
  if (!row.new_active) {
    subject = "Your EFDS account has been deactivated";
    detail = "Your EFDS account has been deactivated. Please contact the EFDS team if this seems incorrect.";
  } else if (!row.previous_active) {
    subject = "Your EFDS account has been reactivated";
    detail = `You can sign in again. Your current access is ${roleName[row.new_role]}.`;
  } else if (row.new_role === "admin" && row.previous_role !== "admin") {
    subject = "Your EFDS administrator access is ready";
    detail = "Your EFDS account now has administrator access.";
  } else if (
    row.new_role === "committee" && row.previous_role !== "committee"
  ) {
    subject = "Your EFDS committee access is ready";
    detail = "Your EFDS account now has committee access.";
  } else if (
    row.new_verification_status === "approved" &&
    row.previous_verification_status !== "approved"
  ) {
    subject = "Your EFDS membership has been verified";
    detail = "Your EFDS membership is verified. You now have access to EFDS member resources as they become available.";
  } else if (
    row.new_verification_status === "declined" &&
    row.previous_verification_status !== "declined"
  ) {
    subject = "Your EFDS standard member access is confirmed";
    detail = "Your account is confirmed as a standard member. EFDS society membership has not been verified, so EFDS member resources are not available to this account. You can still sign in and access events and public resources. If you believe you are an EFDS member, update your membership details in your profile and contact the EFDS team for another review.";
  } else if (row.new_role !== row.previous_role) {
    detail = `Your EFDS account access is now ${roleName[row.new_role]}.`;
  } else if (row.new_officer_id !== row.previous_officer_id) {
    subject = "Your EFDS committee identity has changed";
    detail = row.new_officer_id
      ? "Your EFDS account has been linked to a committee roster identity."
      : "Your EFDS account is no longer linked to a committee roster identity.";
  }
  const followup = row.new_active
    ? `View your account: ${SITE}/dashboard/profile`
    : "Contact the EFDS team: siheon.lee25@imperial.ac.uk";
  const text = `${subject}\n\n${detail}\n\n${followup}\n\nIf you did not expect this change, contact siheon.lee25@imperial.ac.uk.\n\nEFDS is an independent student society at Imperial College London.`;
  const html = `<html><body style="font-family:Arial,sans-serif;color:#17233b;line-height:1.6"><h1 style="font-size:24px">${subject}</h1><p>${detail}</p><p>${
    row.new_active
      ? `<a href="${SITE}/dashboard/profile">View your EFDS account</a>`
      : 'Contact <a href="mailto:siheon.lee25@imperial.ac.uk">the EFDS team</a>'
  }</p><p>If you did not expect this change, contact <a href="mailto:siheon.lee25@imperial.ac.uk">siheon.lee25@imperial.ac.uk</a>.</p><p>EFDS is an independent student society at Imperial College London.</p></body></html>`;
  return {
    to: emailAddress(row.recipient_email),
    subject,
    text,
    html,
    identity: `account-status:${row.id}`,
  };
}

export async function deliverAccountNotice(
  row: AccountNotice,
  finish: (
    id: string,
    provider: Provider,
    result: SendResult,
  ) => Promise<void>,
  send: (provider: Provider, mail: Mail, key: string) => Promise<SendResult>,
) {
  const ledger: Ledger = {
    // claim_account_status_notices has already locked and transitioned this row.
    async claim() { return "claimed"; },
    async finish(_key, provider, result) {
      await finish(row.id, provider, result);
    },
  };
  await deliver(buildAccountNotice(row), ledger, send);
}
