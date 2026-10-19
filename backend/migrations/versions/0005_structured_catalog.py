"""Version-scoped catalog and provenance; existing tenant RLS remains in force."""
from alembic import op

revision = "0005_structured_catalog"
down_revision = "0004_session_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET ROLE app_owner")
    op.execute("""
        ALTER TABLE app.document_versions
          ADD COLUMN catalog_title text NOT NULL DEFAULT '',
          ADD COLUMN catalog_text text NOT NULL DEFAULT '',
          ADD COLUMN chunk_metadata jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('simple'::regconfig, catalog_title), 'A') ||
            setweight(to_tsvector('simple'::regconfig, catalog_text), 'B')) STORED;
        CREATE INDEX document_versions_search_gin ON app.document_versions USING gin(search_vector);
        ALTER TABLE app.query_records
          ADD COLUMN selected_source_ids jsonb NOT NULL DEFAULT '[]',
          ADD COLUMN timings jsonb NOT NULL DEFAULT '{}';
    """)
    op.execute("RESET ROLE")


def downgrade():
    raise RuntimeError("Use a forward migration or tested recovery")
