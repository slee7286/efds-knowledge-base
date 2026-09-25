"""Assign the ACTION-### reference to every new committee ticket.

Committee tickets are `operational_records` rows with `record_type = 'action_item'`.
Their reference (`ACTION-023`) is not a column: it is the leading part of the title
text, and every consumer reads it back out of the title:

  * `lib/tickets/activity.ts` extracts it to match a ticket to the Slack thread that
    discusses it,
  * `components/slack/search-page.tsx` treats a message as a ticket when its text
    starts with that token,
  * `lib/db/tickets.ts` finds cross-channel references by searching for `ACTION-`.

Nothing generated that leading token, so tickets created through the dashboard, and
tickets created from reviewed evidence, arrived without one and were invisible to all
of the matching above. The reference therefore has to be assigned where the ticket is
created - in the database - rather than in the Next.js server action, so that every
creation path gets one without each caller having to remember to ask.

`BEFORE INSERT` on the table is the narrowest place that satisfies that. The trigger
only touches `action_item` rows, and leaves a title that already carries a reference
exactly as written, so Slack-authored and replayed titles keep their code. A
transaction-scoped advisory lock serialises the max-plus-one read, because two
concurrent inserts would otherwise choose the same number.

Patterns are written with POSIX character classes (`[[:digit:]]`, `[[:space:]]`)
rather than the shorter `slash-d` / `slash-s` shorthands. The shorthand form needs a
literal backslash to survive the trip from this Python file, through the migration
runner, into a SQL string literal, and a doubled backslash there silently becomes a
regex that matches a backslash character - which fails closed on every row and assigns
every ticket the number 001. The POSIX classes remove the backslash from the SQL
entirely, so there is nothing left to escape.

Codes are three digits by convention. `lpad` widens past `999` instead of failing, and
the readers' three-digit pattern would need widening if the committee ever passes that
count.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024_committee_ticket_reference"
down_revision: str | None = "0023_public_retrieval_and_multi_query"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Patterns live inline in the SQL below rather than in Python constants, so the exact
# text that reaches the database is visible in one place and cannot drift.
ASSIGN_REFERENCE = """
CREATE OR REPLACE FUNCTION public.assign_committee_ticket_reference()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
  v_next integer;
BEGIN
  IF NEW.record_type IS DISTINCT FROM 'action_item' THEN
    RETURN NEW;
  END IF;
  IF NEW.title ~* '^[[:space:]]*ACTION-[[:digit:]]{3}([^[:digit:]]|$)' THEN
    RETURN NEW;
  END IF;
  -- Serialise the max-plus-one read: concurrent inserts would otherwise share a number.
  PERFORM pg_advisory_xact_lock(hashtext('public.assign_committee_ticket_reference'));
  SELECT coalesce(max((regexp_match(upper(r.title), 'ACTION-([[:digit:]]{3})'))[1]::integer), 0) + 1
    INTO v_next
    FROM public.operational_records r
   WHERE r.record_type = 'action_item'
     AND upper(r.title) ~ 'ACTION-[[:digit:]]{3}';
  NEW.title := 'ACTION-' || lpad(v_next::text, 3, '0') || ' — ' || btrim(NEW.title);
  RETURN NEW;
END;
$function$;
"""

BACKFILL_REFERENCES = """
WITH base AS (
  SELECT coalesce(max((regexp_match(upper(r.title), 'ACTION-([[:digit:]]{3})'))[1]::integer), 0) AS n
    FROM public.operational_records r
   WHERE r.record_type = 'action_item'
     AND upper(r.title) ~ 'ACTION-[[:digit:]]{3}'
), missing AS (
  SELECT r.id, row_number() OVER (ORDER BY r.created_at, r.id) AS rn
    FROM public.operational_records r
   WHERE r.record_type = 'action_item'
     AND r.title !~* '^[[:space:]]*ACTION-[[:digit:]]{3}([^[:digit:]]|$)'
)
UPDATE public.operational_records t
   SET title = 'ACTION-' || lpad((base.n + missing.rn)::text, 3, '0') || ' — ' || btrim(t.title)
  FROM base, missing
 WHERE t.id = missing.id
"""

CREATE_TRIGGER = """
DROP TRIGGER IF EXISTS assign_committee_ticket_reference ON public.operational_records;
CREATE TRIGGER assign_committee_ticket_reference
  BEFORE INSERT ON public.operational_records
  FOR EACH ROW
  WHEN (NEW.record_type = 'action_item')
  EXECUTE FUNCTION public.assign_committee_ticket_reference();
"""


def upgrade() -> None:
    # Existing tickets come first, so the trigger's max-plus-one continues the numbering
    # rather than colliding with a code that is already in use.
    op.execute(BACKFILL_REFERENCES)
    op.execute(ASSIGN_REFERENCE)
    op.execute(CREATE_TRIGGER)
    # The trigger fires from whoever inserts the row, so the function is never called as
    # an RPC. Revoke the implicit PUBLIC grant anyway: this repo's hardening migrations
    # (0018, 0023) treat an unrevoked PUBLIC grant as a live defect.
    op.execute("REVOKE ALL ON FUNCTION public.assign_committee_ticket_reference() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS assign_committee_ticket_reference ON public.operational_records")
    op.execute("DROP FUNCTION IF EXISTS public.assign_committee_ticket_reference()")
    # Titles keep their codes. A code is part of the title text, and hand-authored titles
    # carry codes the committee wrote themselves, so stripping them here cannot be done
    # without guessing which ones this migration added.
