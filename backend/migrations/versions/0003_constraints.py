"""Enforce version ownership and role vocabulary in PostgreSQL."""
from alembic import op

revision = "0003_constraints"
down_revision = "0002_privileges"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE app.document_versions ADD CONSTRAINT version_document_identity UNIQUE(tenant_id,document_id,id)")
    op.execute("ALTER TABLE app.documents ADD CONSTRAINT active_version_document_fk FOREIGN KEY(tenant_id,id,active_version_id) REFERENCES app.document_versions(tenant_id,document_id,id) DEFERRABLE INITIALLY DEFERRED")
    op.execute("ALTER TABLE app.jobs ADD CONSTRAINT job_version_tenant_fk FOREIGN KEY(tenant_id,version_id) REFERENCES app.document_versions(tenant_id,id)")
    op.execute("ALTER TABLE app.memberships ADD CONSTRAINT valid_membership_role CHECK(role IN ('viewer','editor','tenant_admin','auditor'))")
    op.execute("ALTER TABLE app.document_versions ADD CONSTRAINT valid_source_bytes CHECK(bytes>0)")


def downgrade():
    raise RuntimeError("Use forward migration or tested recovery")
