"""Initial application schema, fail-closed RLS, append-only audit, durable job claims."""
from pathlib import Path

from alembic import op

revision = "0001_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION app_owner")
    op.execute("SET ROLE app_owner")
    op.execute(Path(__file__).with_name("0001_schema.sql").read_text())
    tenant = "tenant_id::text = current_setting('app.tenant_id', true)"
    policies = {
        "tenants": "id::text = current_setting('app.tenant_id', true) OR id IN (SELECT tenant_id FROM app.memberships WHERE user_id=current_setting('app.user_id',true) AND active)",
        "users": "id=current_setting('app.user_id',true) OR id IN (SELECT user_id FROM app.memberships WHERE tenant_id::text=current_setting('app.tenant_id',true))",
        "memberships": f"({tenant}) OR user_id=current_setting('app.user_id',true)",
        "sessions": "token_hash=current_setting('app.session_hash',true)",
    }
    tables = ('audit_events', 'evaluation_runs', 'query_records', 'tenants', 'users', 'audit_deliveries', 'documents', 'memberships', 'sessions', 'document_versions', 'jobs', 'index_builds')
    for table in tables:
        policy = policies.get(table, tenant)
        op.execute(f'ALTER TABLE app."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE app."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation ON app."{table}" USING ({policy}) WITH CHECK ({policy})')
    op.execute("GRANT USAGE ON SCHEMA app TO rag_app, rag_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO rag_app, rag_worker")
    op.execute("REVOKE UPDATE, DELETE ON app.audit_events FROM rag_app, rag_worker")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON app.tenants, app.memberships FROM rag_app")
    op.execute("""
    CREATE FUNCTION app.reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'audit events are append-only'; END $$;
    CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON app.audit_events
      FOR EACH ROW EXECUTE FUNCTION app.reject_audit_mutation();
    """)
    # A narrow SECURITY DEFINER function claims across tenant queues without granting BYPASSRLS.
    op.execute("""
    CREATE FUNCTION app.claim_job(lease_seconds integer, max_attempts integer)
    RETURNS TABLE(job_id uuid, tenant uuid, token uuid) LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = pg_catalog, app SET row_security = off AS $$
    DECLARE picked app.jobs%ROWTYPE; new_token uuid;
    BEGIN
      IF lease_seconds < 10 OR lease_seconds > 3600 OR max_attempts < 1 OR max_attempts > 20 THEN
        RAISE EXCEPTION 'invalid lease configuration';
      END IF;
      UPDATE app.jobs SET status='dead_letter', error='LeaseExpired', lease_token=NULL
        WHERE status='processing' AND lease_until < clock_timestamp() AND attempts >= max_attempts;
      SELECT * INTO picked FROM app.jobs
      WHERE ((status IN ('queued','retry_wait') AND available_at <= clock_timestamp())
        OR (status='processing' AND lease_until < clock_timestamp()))
        AND attempts < max_attempts
      ORDER BY available_at, created_at FOR UPDATE SKIP LOCKED LIMIT 1;
      IF NOT FOUND THEN RETURN; END IF;
      new_token := gen_random_uuid();
      UPDATE app.jobs SET status='processing', attempts=attempts+1, lease_token=new_token,
        lease_until=clock_timestamp()+make_interval(secs=>lease_seconds) WHERE id=picked.id;
      RETURN QUERY SELECT picked.id, picked.tenant_id, new_token;
    END $$;
    REVOKE ALL ON FUNCTION app.claim_job(integer,integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION app.claim_job(integer,integer) TO rag_worker;
    """)
    op.execute("RESET ROLE")
    op.execute("GRANT USAGE ON SCHEMA app TO rag_maintenance")
    op.execute("GRANT SELECT, UPDATE ON app.jobs TO rag_maintenance")
    # The function needs a separately privileged owner because FORCE RLS applies to app_owner.
    # This administrative role owns only audited queue/export functions, never a runtime login.
    op.execute("ALTER FUNCTION app.claim_job(integer,integer) OWNER TO rag_maintenance")


def downgrade():
    raise RuntimeError("Destructive downgrade is intentionally disabled; restore/forward-fix using the runbook")
