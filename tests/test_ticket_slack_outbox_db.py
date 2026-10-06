"""Opt-in outbox SQL smoke test; ONLY a disposable local efds_test_* database.

Set EFDS_TEST_DATABASE_URL, never DATABASE_URL. This test creates and drops tables
in public, so the name and loopback-host guard are intentionally strict.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from efds.integrations.ticket_slack_publisher import Publisher
from efds.integrations.ticket_slack_store import OutboxStore


@pytest.mark.integration
def test_outbox_trigger_and_rls_on_disposable_postgres():
    dsn = os.getenv("EFDS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set EFDS_TEST_DATABASE_URL to a disposable local efds_test_* database")
    info = conninfo_to_dict(dsn)
    if not info.get("dbname", "").startswith("efds_test_") or info.get("host") not in ("localhost", "127.0.0.1"):
        pytest.fail("Refusing to write to any non-local/non-test database")
    migration = Path(__file__).resolve().parents[1] / "alembic/versions/0025_website_ticket_slack_outbox.py"
    spec = importlib.util.spec_from_file_location("outbox_migration", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with psycopg.connect(dsn) as db:
        with db.cursor() as cur:
            # This database is explicitly disposable; recreate its public schema
            # so repeated local/full-suite runs test the same clean migration.
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public")
            cur.execute("""
                DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF;
                END $$;
                CREATE TABLE public.operational_records (
                  id uuid PRIMARY KEY, record_type text, review_status text, is_current boolean,
                  visibility text, metadata jsonb, created_at timestamptz DEFAULT now(),
                  title text, execution_status text, due_at timestamptz
                );
                CREATE TABLE public.operational_review_events (
                  id uuid PRIMARY KEY, operational_record_id uuid REFERENCES public.operational_records(id), action text
                );
                CREATE TABLE public.officers (id uuid PRIMARY KEY, name text);
                CREATE TABLE public.operational_ticket_assignees (ticket_id uuid, officer_id uuid);
            """)
            old_ticket, new_ticket, slack_ticket, suggested_ticket = uuid4(), uuid4(), uuid4(), uuid4()
            cur.execute("""
                INSERT INTO public.operational_records
                  (id,record_type,review_status,is_current,visibility,metadata,title,execution_status,created_at)
                VALUES (%s,'action_item','approved',true,'committee','{"origin":"committee_dashboard"}','Old','open',now()-interval '1 day'),
                       (%s,'action_item','approved',true,'committee','{"origin":"slack_actions_tickets"}','Slack','open',now()-interval '1 day')
            """, (old_ticket, slack_ticket))
            cur.execute(module.OUTBOX_SQL)
            cur.execute("SELECT count(*) FROM public.ticket_slack_outbox")
            assert cur.fetchone()[0] == 0  # no migration backfill
        db.commit()  # deployment transaction commits before any website RPC can create
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO public.operational_records
                  (id,record_type,review_status,is_current,visibility,metadata,title,execution_status)
                VALUES (%s,'action_item','approved',true,'committee','{"origin":"committee_dashboard"}',
                        'ACTION-025 — <Hello>','open')
            """, (new_ticket,))
            for ticket, action in ((old_ticket, 'committee_create'),
                                   (old_ticket, 'committee_status'),
                                   (slack_ticket, 'committee_create'),
                                   (new_ticket, 'committee_create')):
                cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) VALUES (%s,%s,%s)",
                            (uuid4(), ticket, action))
            cur.execute("UPDATE public.operational_records SET execution_status='completed' WHERE id=%s", (new_ticket,))
            cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) VALUES (%s,%s,'committee_status')",
                        (uuid4(), new_ticket))
            cur.execute("""
                INSERT INTO public.operational_records
                  (id,record_type,review_status,is_current,visibility,metadata,title,execution_status)
                VALUES (%s,'action_item','approved',true,'committee','{"origin":"committee_dashboard"}',
                        'ACTION-026 — Suggested work','open')
            """, (suggested_ticket,))
            cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) VALUES (%s,%s,'committee_create')",
                        (uuid4(), suggested_ticket))
            cur.execute("UPDATE public.operational_records SET metadata=jsonb_set(metadata,'{origin}',to_jsonb('committee_agent_suggestion'::text)) WHERE id=%s",
                        (suggested_ticket,))
            cur.execute("SELECT action,snapshot->>'title',state FROM public.ticket_slack_outbox ORDER BY id")
            assert cur.fetchall() == [
                ('committee_create', 'ACTION-025 — <Hello>', 'pending'),
                ('committee_status', 'ACTION-025 — <Hello>', 'pending'),
            ]
            cur.execute("SELECT snapshot->>'execution_status' FROM public.ticket_slack_outbox ORDER BY id")
            assert [item[0] for item in cur.fetchall()] == ['open', 'completed']
            cur.execute('SAVEPOINT rolled_back_mutation')
            cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) VALUES (%s,%s,'committee_update')",
                        (uuid4(), new_ticket))
            cur.execute('ROLLBACK TO SAVEPOINT rolled_back_mutation')
            cur.execute('SELECT count(*) FROM public.ticket_slack_outbox')
            assert cur.fetchone()[0] == 2
        db.commit()
        class FakeSlack:
            def __init__(self):
                self.messages = []
                self.posts = []
                self.updates = []
                self.fail_after_post = False

            def history(self, channel):
                return list(self.messages)

            def replies(self, channel, root):
                return list(self.messages)

            def post_message(self, channel, text, *, thread_ts=None):
                ts = f"123.{len(self.messages)+1:06}"
                self.posts.append((channel, thread_ts))
                self.messages.append({'ts': ts, 'text': text})
                if self.fail_after_post:
                    raise TimeoutError('response lost after acceptance')
                return ts

            def update_message(self, channel, ts, text):
                self.updates.append((channel, ts, text))

        slack = FakeSlack()
        publisher = Publisher(OutboxStore(db), slack, "https://www.imperial-efds.com")
        assert publisher.run_once() == "posted"
        assert publisher.run_once() == "posted"
        assert publisher.run_once() == "empty"
        assert slack.posts == [('C0BQPDP5T44', None), ('C0BQPDP5T44', '123.000001')]
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.ticket_slack_outbox WHERE state='posted'")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT root_ts FROM public.ticket_slack_roots WHERE ticket_id=%s", (new_ticket,))
            assert cur.fetchone()[0] == '123.000001'
        db.commit()
        # The external post happened but its HTTP acknowledgement was lost. The
        # persisted uncertain claim must reconcile the marker, not send again.
        with db.cursor() as cur:
            cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) VALUES (%s,%s,'committee_update')",
                        (uuid4(), new_ticket))
        db.commit()
        slack.fail_after_post = True
        assert publisher.run_once() == 'uncertain'
        assert len(slack.posts) == 3
        with db.cursor() as cur:
            cur.execute("UPDATE public.ticket_slack_outbox SET retry_at=now()-interval '1 minute' WHERE state='uncertain'")
        db.commit()
        slack.fail_after_post = False
        assert publisher.run_once() == 'reconciled'
        assert len(slack.posts) == 3
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.ticket_slack_outbox WHERE state='posted'")
            assert cur.fetchone()[0] == 3
        db.commit()
        try:
            with db.cursor() as cur:
                cur.execute("SET ROLE authenticated")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cur.execute("SELECT count(*) FROM public.ticket_slack_outbox")
        finally:
            db.rollback()
    # Database/container is disposable and removed by test operator.
