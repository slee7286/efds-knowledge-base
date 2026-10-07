"""Opt-in SQL behavior check, restricted to an explicitly disposable loopback DB."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from efds.integrations.ticket_slack_store import OutboxStore


def load_migration(filename):
    path = Path(__file__).resolve().parents[1] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location(filename, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
def test_edge_migration_claims_same_outbox_as_python_and_guards_rpcs():
    dsn = os.getenv("EFDS_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set EFDS_TEST_DATABASE_URL to a disposable local efds_test_* database")
    info = conninfo_to_dict(dsn)
    if not info.get("dbname", "").startswith("efds_test_") or info.get("host") not in ("localhost", "127.0.0.1"):
        pytest.fail("Refusing to write to any non-local/non-test database")
    with psycopg.connect(dsn) as db:
        with db.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE; DROP SCHEMA IF EXISTS vault CASCADE; "
                        "DROP SCHEMA IF EXISTS net CASCADE; DROP SCHEMA IF EXISTS cron CASCADE; "
                        "DROP SCHEMA IF EXISTS extensions CASCADE; CREATE SCHEMA public; "
                        "CREATE SCHEMA vault; CREATE SCHEMA net; CREATE SCHEMA cron; CREATE SCHEMA extensions")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions")
            cur.execute("""
                DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF;
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF;
                END $$;
                CREATE TABLE public.operational_records (
                  id uuid PRIMARY KEY, record_type text, review_status text, is_current boolean,
                  visibility text, metadata jsonb, created_at timestamptz DEFAULT now(),
                  title text, description text, execution_status text, due_at timestamptz
                );
                CREATE TABLE public.operational_review_events (
                  id uuid PRIMARY KEY, operational_record_id uuid REFERENCES public.operational_records(id), action text
                );
                CREATE TABLE public.officers (id uuid PRIMARY KEY, name text);
                CREATE TABLE public.operational_ticket_assignees (ticket_id uuid, officer_id uuid);
                CREATE TABLE vault.secrets (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text UNIQUE, secret text);
                CREATE VIEW vault.decrypted_secrets AS SELECT name,secret AS decrypted_secret FROM vault.secrets;
                CREATE FUNCTION vault.create_secret(p_secret text,p_name text)
                  RETURNS uuid LANGUAGE plpgsql AS $$ DECLARE v_id uuid; BEGIN
                    INSERT INTO vault.secrets(name,secret) VALUES (p_name,p_secret) RETURNING id INTO v_id;
                    RETURN v_id; END $$;
                CREATE TABLE net.calls (id bigint GENERATED ALWAYS AS IDENTITY,url text,headers jsonb);
                CREATE FUNCTION net.http_post(url text,headers jsonb,body jsonb,timeout_milliseconds integer)
                  RETURNS bigint LANGUAGE plpgsql AS $$ DECLARE v_id bigint; BEGIN
                    INSERT INTO net.calls(url,headers) VALUES (url,headers) RETURNING id INTO v_id;
                    RETURN v_id; END $$;
                CREATE TABLE cron.job (jobid bigint GENERATED ALWAYS AS IDENTITY,jobname text UNIQUE,schedule text,command text);
                CREATE FUNCTION cron.schedule(name text, schedule text, command text)
                  RETURNS bigint LANGUAGE plpgsql AS $$ DECLARE v_id bigint; BEGIN
                    INSERT INTO cron.job(jobname,schedule,command) VALUES (name,schedule,command)
                    RETURNING jobid INTO v_id; RETURN v_id; END $$;
            """)
            cur.execute(load_migration("0025_website_ticket_slack_outbox.py").OUTBOX_SQL)
            cur.execute(load_migration("0026_ticket_slack_edge_worker.py").SQL)
            cur.execute(load_migration("0027_ticket_slack_description.py").SQL)
        db.commit()
        with db.cursor() as cur:
            cur.execute("SAVEPOINT cancelled_ticket")
            rolled_back = uuid4()
            cur.execute("""
                INSERT INTO public.operational_records
                  (id,record_type,review_status,is_current,visibility,metadata,title,execution_status)
                VALUES (%s,'action_item','approved',true,'committee',
                        '{"origin":"committee_dashboard"}','Cancelled create','open')
            """, (rolled_back,))
            cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) "
                        "VALUES (%s,%s,'committee_create')", (uuid4(), rolled_back))
            cur.execute("ROLLBACK TO SAVEPOINT cancelled_ticket")
            cur.execute("SELECT count(*) FROM net.calls")
            assert cur.fetchone()[0] == 0
        db.commit()
        ticket, first, second = uuid4(), uuid4(), uuid4()
        with db.cursor() as cur:
            cur.execute("""
                INSERT INTO public.operational_records
                  (id,record_type,review_status,is_current,visibility,metadata,title,description,execution_status)
                VALUES (%s,'action_item','approved',true,'committee',
                        '{"origin":"committee_dashboard"}','New ticket','Use the approved event checklist','open')
            """, (ticket,))
            cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) "
                        "VALUES (%s,%s,'committee_create')", (first, ticket))
            cur.execute("SELECT count(*) FROM net.calls")
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT snapshot->>'description' FROM public.ticket_slack_outbox WHERE event_id=%s", (first,))
            assert cur.fetchone() == ("Use the approved event checklist",)
            cur.execute("UPDATE public.operational_records SET description=%s WHERE id=%s",
                        ("Updated checklist", ticket))
            cur.execute("INSERT INTO public.operational_review_events(id,operational_record_id,action) "
                        "VALUES (%s,%s,'committee_update')", (second, ticket))
            cur.execute("SELECT snapshot->>'description' FROM public.ticket_slack_outbox WHERE event_id=%s", (second,))
            assert cur.fetchone() == ("Updated checklist",)
        db.commit()
        with db.cursor() as cur:
            cur.execute("SELECT public.verify_ticket_slack_worker('not-a-token')")
            assert cur.fetchone()[0] is False
            # Only synthetic local fixture bytes; never read a Vault secret value.
            cur.execute("UPDATE vault.secrets SET secret=%s WHERE name='efds_ticket_slack_worker'",
                        ("a" * 64,))
            cur.execute("SELECT public.verify_ticket_slack_worker(%s)", ("a" * 64,))
            assert cur.fetchone()[0] is True
            cur.execute("SELECT public.claim_ticket_slack_event()")
            claimed = cur.fetchone()[0]
            assert claimed["event_id"] == str(first)
            assert claimed["reconcile_only"] is False
            # A concurrent Python publisher cannot take the locked create or
            # skip ahead to its later change while the Edge claim is uncommitted.
            with psycopg.connect(dsn) as concurrent:
                assert OutboxStore(concurrent).claim() is None
        db.commit()  # claim committed before a hypothetical external POST
        assert OutboxStore(db).claim() is None  # second ticket event blocked behind create
        with db.cursor() as cur:
            cur.execute("SELECT public.finish_ticket_slack_event(%s,%s,%s)", (first, "123.000001", "123.000001"))
        db.commit()
        next_claim = OutboxStore(db).claim()
        assert next_claim is not None and next_claim["event_id"] == str(second)
        assert next_claim["root_ts"] == "123.000001"
        with db.cursor() as cur:
            cur.execute("SELECT public.defer_ticket_slack_event(%s,true)", (second,))
            cur.execute("UPDATE public.ticket_slack_outbox SET retry_at=now()-interval '1 minute' WHERE event_id=%s", (second,))
        db.commit()
        with db.cursor() as cur:
            cur.execute("SELECT public.claim_ticket_slack_event()")
            retry = cur.fetchone()[0]
            assert retry["reconcile_only"] is True and retry["root_ts"] == "123.000001"
        db.commit()
        try:
            with db.cursor() as cur:
                cur.execute("SET ROLE authenticated")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cur.execute("SELECT public.claim_ticket_slack_event()")
        finally:
            db.rollback()
