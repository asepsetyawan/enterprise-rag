"""Keep API out of the audit delivery control plane; reconcile exhausted leases."""
from alembic import op

revision = "0002_privileges"
down_revision = "0001_platform"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("REVOKE UPDATE, DELETE ON app.audit_deliveries FROM rag_app")
    op.execute("GRANT USAGE ON SCHEMA app TO rag_maintenance")
    op.execute("GRANT SELECT, UPDATE ON app.jobs TO rag_maintenance")
    op.execute("""
    CREATE OR REPLACE FUNCTION app.claim_job(lease_seconds integer, max_attempts integer)
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
        OR (status='processing' AND lease_until < clock_timestamp())) AND attempts < max_attempts
      ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1;
      IF NOT FOUND THEN RETURN; END IF;
      new_token := gen_random_uuid();
      UPDATE app.jobs SET status='processing', attempts=attempts+1,lease_token=new_token,
        lease_until=clock_timestamp()+make_interval(secs=>lease_seconds) WHERE id=picked.id;
      RETURN QUERY SELECT picked.id,picked.tenant_id,new_token;
    END $$;
    REVOKE ALL ON FUNCTION app.claim_job(integer,integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION app.claim_job(integer,integer) TO rag_worker;
    ALTER FUNCTION app.claim_job(integer,integer) OWNER TO rag_maintenance;
    """)


def downgrade():
    raise RuntimeError("Use a forward migration or tested recovery")
