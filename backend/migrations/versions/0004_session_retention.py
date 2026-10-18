"""Bound session metadata retention without widening runtime RLS privileges."""
from alembic import op

revision = "0004_session_retention"
down_revision = "0003_constraints"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("GRANT SELECT,DELETE ON app.sessions TO rag_maintenance")
    op.execute("""
    CREATE FUNCTION app.prune_expired_sessions() RETURNS bigint
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,app SET row_security=off AS $$
      WITH expired AS (SELECT token_hash FROM app.sessions WHERE expires_at < clock_timestamp() LIMIT 50000),
      removed AS (DELETE FROM app.sessions WHERE token_hash IN (SELECT token_hash FROM expired) RETURNING 1)
      SELECT count(*) FROM removed;
    $$;
    REVOKE ALL ON FUNCTION app.prune_expired_sessions() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION app.prune_expired_sessions() TO rag_worker;
    ALTER FUNCTION app.prune_expired_sessions() OWNER TO rag_maintenance;
    """)


def downgrade():
    raise RuntimeError("Use forward migration or tested recovery")
