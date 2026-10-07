"""Contract of the transactional pg_net wake-up and shared outbox worker."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def migration():
    path = ROOT / "alembic/versions/0026_ticket_slack_edge_worker.py"
    spec = importlib.util.spec_from_file_location("ticket_slack_edge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wakeup_is_transactional_and_cron_recovers_without_github_dispatch():
    m = migration()
    assert m.down_revision == "0025_website_ticket_slack_outbox"
    sql = m.SQL
    assert "AFTER INSERT ON public.ticket_slack_outbox" in sql
    assert "net.http_post(" in sql
    assert "vault.decrypted_secrets" in sql
    assert "WHEN OTHERS" in sql  # wake-up failure never rolls back ticket save
    assert "'* * * * *'" in sql
    assert "send-ticket-slack" in sql
    assert "efds_ticket_slack_worker" in sql


def test_edge_rpcs_have_service_role_only_grants_and_shared_claim_order():
    sql = migration().SQL
    for signature in ("verify_ticket_slack_worker(text)", "claim_ticket_slack_event()",
                      "finish_ticket_slack_event(uuid,text,text)",
                      "defer_ticket_slack_event(uuid,boolean)"):
        assert f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, anon, authenticated" in sql
        assert f"GRANT EXECUTE ON FUNCTION public.{signature} TO service_role" in sql
    assert "FOR UPDATE OF o SKIP LOCKED" in sql
    assert "earlier.state <> 'posted'" in sql
    assert "candidate.previous_state <> 'pending'" in sql
    assert "claimed_at<now()-interval '2 hours'" in sql
    assert "ticket_slack_roots" in sql
