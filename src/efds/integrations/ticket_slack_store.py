"""PostgreSQL delivery claims and acknowledgements for website-origin tickets."""
from __future__ import annotations

from typing import Any


class OutboxStore:
    def __init__(self, connection: Any):
        self.connection = connection

    def claim(self) -> dict[str, Any] | None:
        # Do not hold a database transaction across external HTTP. A stale sending
        # claim or uncertain outcome is *reconcile only*, never another POST.
        with self.connection.cursor() as cur:
            cur.execute("""
                WITH candidate AS (
                  SELECT o.id,o.state AS previous_state,r.root_ts,r.create_event_id
                  FROM public.ticket_slack_outbox o
                  LEFT JOIN public.ticket_slack_roots r ON r.ticket_id=o.ticket_id
                  WHERE o.retry_at<=now()
                    AND (o.state IN ('pending','uncertain') OR
                         (o.state='sending' AND o.claimed_at<now()-interval '2 hours'))
                    AND NOT EXISTS (
                      SELECT 1 FROM public.ticket_slack_outbox earlier
                      WHERE earlier.ticket_id=o.ticket_id AND earlier.id < o.id
                        AND earlier.state <> 'posted'
                    )
                    AND (o.action='committee_create' OR r.root_ts IS NOT NULL)
                  ORDER BY o.id
                  FOR UPDATE OF o SKIP LOCKED
                  LIMIT 1
                )
                UPDATE public.ticket_slack_outbox target
                   SET state=CASE WHEN candidate.previous_state='uncertain' THEN 'uncertain' ELSE 'sending' END,
                       claimed_at=now(),retry_at=now()+interval '15 minutes',
                       attempts=target.attempts+1
                  FROM candidate WHERE target.id=candidate.id
                RETURNING target.event_id,target.ticket_id,target.action,target.snapshot,
                          target.channel,candidate.root_ts,candidate.create_event_id,
                          candidate.previous_state <> 'pending' AS reconcile_only
            """)
            result = cur.fetchone()
        self.connection.commit()
        if result is None:
            return None
        event_id, ticket_id, action, snapshot, channel, root_ts, create_event_id, reconcile_only = result
        return {"event_id": str(event_id), "ticket_id": str(ticket_id), "action": action,
                "snapshot": snapshot, "channel": channel, "root_ts": root_ts,
                "root_event_id": str(create_event_id) if create_event_id else None,
                "reconcile_only": reconcile_only}

    def delivered(self, event: dict[str, Any], ts: str, root_ts: str) -> None:
        with self.connection.cursor() as cur:
            if event["action"] == "committee_create":
                cur.execute("""
                    INSERT INTO public.ticket_slack_roots(ticket_id,create_event_id,channel,root_ts)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (ticket_id) DO NOTHING
                """, (event["ticket_id"], event["event_id"], event["channel"], root_ts))
            cur.execute("""
                UPDATE public.ticket_slack_outbox
                   SET state='posted',slack_ts=%s,delivered_at=now()
                 WHERE event_id=%s AND state IN ('sending','uncertain')
            """, (ts, event["event_id"]))
            if getattr(cur, "rowcount", 1) != 1:
                raise RuntimeError("Delivery claim changed before acknowledgement")
        self.connection.commit()

    def uncertain_delivery(self, event: dict[str, Any]) -> None:
        with self.connection.cursor() as cur:
            cur.execute("""
                UPDATE public.ticket_slack_outbox
                   SET state='uncertain',retry_at=now()+interval '15 minutes'
                 WHERE event_id=%s AND state IN ('sending','uncertain')
            """, (event["event_id"],))
        self.connection.commit()

    def retry_later(self, event: dict[str, Any]) -> None:
        if event.get("reconcile_only"):
            self.uncertain_delivery(event)
            return
        with self.connection.cursor() as cur:
            cur.execute("""
                UPDATE public.ticket_slack_outbox
                   SET state='pending',retry_at=now()+
                       (least(3600,30*power(2,least(attempts,7))) * interval '1 second')
                 WHERE event_id=%s AND state='sending'
            """, (event["event_id"],))
        self.connection.commit()
