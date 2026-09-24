# Sender-limited Outlook evidence

The optional Outlook collector reads one delegated Microsoft 365 mailbox with
Microsoft Graph. It requests messages from exactly two configured sender
addresses, then checks the `from` address again before storing any message.
It never lists unfiltered mail, downloads attachments, sends messages, moves
mail, or writes to Outlook. This is independent of EFDS website login.

The mailbox and two sender addresses belong only in the ignored local `.env`
or another private environment. Do not put them in this repository, GitHub
workflow logs, screenshots, or issue comments. The current source is a
personal mailbox, so the collector is not scheduled in GitHub Actions.
The empty database tables and admin-only evidence RPC are migrated in
Supabase; no live mailbox messages have been collected.

## Connection

Register a Microsoft Entra public client application in the mailbox's tenant,
enable the device-code/public-client flow, and grant **delegated** `Mail.Read`
and `User.Read`. No application permission, client secret, or `Mail.ReadWrite`
is needed. `Mail.Read` technically permits reading the whole mailbox; the
sender boundary is enforced by the collector's Graph query and a second local
check, not by the OAuth permission itself. A dedicated society mailbox would
reduce that underlying permission scope in the future.

Set these values in the ignored backend `.env`, supplying the real values
privately on the machine that runs the collector:

```text
OUTLOOK_CLIENT_ID=<Entra public-client application UUID>
OUTLOOK_TENANT_ID=<Entra tenant UUID or domain>
OUTLOOK_EXPECTED_MAILBOX_EMAIL=<the delegated mailbox address>
OUTLOOK_ALLOWED_SENDERS=<first exact sender>,<second exact sender>
```

The collector requires exactly two distinct addresses. It verifies the
delegated Graph `/me` identity against `OUTLOOK_EXPECTED_MAILBOX_EMAIL` before
reading messages. It uses a short, bounded Graph query per sender, with an
incremental checkpoint and three-day overlap. A successful first run covers
the previous 90 days unless `--since` specifies an earlier UTC date. A failed
or truncated query does not advance the checkpoint.

In Linux or WSL:

```bash
cd /home/siheon/projects/efds-knowledge-base
uv pip install --python .venv/bin/python -e '.[outlook]'
.venv/bin/python scripts/sync_outlook.py --authorize --check
.venv/bin/python scripts/sync_outlook.py --dry-run
.venv/bin/python scripts/sync_outlook.py
```

The first command in the sign-in flow displays a device code for you to enter
at Microsoft's official page. The refresh-token cache is stored outside the
repository at `~/.local/share/efds/outlook-token-cache.json` with owner-only
permissions. Never send that file or its contents to anyone. Later syncs use
the cache silently; a revoked or expired grant stops the job and requires
`--authorize` again. Use `--check` to verify identity without fetching mail.

## Data boundary and deletion behavior

Only the immutable Graph message ID, Internet message ID, exact sender,
subject, up to 50,000 characters of text body, timestamps, and a safe Outlook
deep link are stored in `outlook_messages`. No recipients, attachments or raw
Graph JSON are stored. An admin-only ingestion run records aggregate counts,
without message content or the mailbox address. The checkpoint is scoped to
the Graph mailbox ID and sender; the personal email address is not in the
checkpoint. RLS allows only active EFDS admins to read the source table.

Known allowed-sender message IDs are rechecked individually. Two `404`
observations at least six hours apart mark a message deleted. The retrieval
index then retires its source units. A later successful read restores the
message. This avoids using Graph message delta, whose server filter cannot
restrict changes to the two senders. It also avoids treating an empty sender
query as proof that every old message was deleted. Rechecks rotate through a
maximum of 200 older messages per sender per run; a due second `404` is
prioritized. This keeps Graph request volume bounded as the archive grows,
but source deletions can take multiple runs to appear in EFDS, especially
when a sender has a large history or the collector is offline. The sync
output and ingestion run report the number rechecked.

`outlook_message` units remain `internal` and are searchable only by admins.
The admin ticket panel enables Outlook suggestions only after a successful
sender sync. Cited ideas enter the existing private operational review flow;
they are not automatically published to committee tickets. Committee members
cannot retrieve raw Outlook evidence through the agent or site.

The collector is not live until a public-client app, delegated consent and a
real sender-limited sync have been verified. For regular unattended collection,
use a host that remains on and can protect the token cache; a sleeping laptop
cannot meet a twelve-hour schedule. Monitor nonzero exits and reauthorize when
required. A university-approved society mailbox and restricted application
access would be the stronger long-term unattended setup.

Sources: [Microsoft Graph message list and permissions](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0),
[sender filtering](https://learn.microsoft.com/en-us/graph/filter-query-parameter),
[immutable message IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id),
[delta-query filter limits](https://learn.microsoft.com/en-us/graph/delta-query-messages),
[MSAL Python device-code flow](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens).
