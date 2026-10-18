import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "app"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "app"}
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = {"schema": "app"}
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.tenants.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app.users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(30), default="viewer")
    groups: Mapped[list] = mapped_column(JSONB, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class LoginSession(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "app"}
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app.users.id"))
    csrf: Mapped[str] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), {"schema": "app"})
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.tenants.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("app.users.id"))
    title: Mapped[str] = mapped_column(String(240))
    filename: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    acl_users: Mapped[list] = mapped_column(JSONB, default=list)
    acl_groups: Mapped[list] = mapped_column(JSONB, default=list)
    acl_revision: Mapped[int] = mapped_column(Integer, default=1)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "document_id"], ["app.documents.tenant_id", "app.documents.id"]),
        UniqueConstraint("tenant_id", "id"), {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    checksum: Mapped[str] = mapped_column(String(64))
    relative_path: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(240))
    bytes: Mapped[int] = mapped_column(Integer)
    workspace: Mapped[str | None] = mapped_column(String(160), nullable=True)
    profile: Mapped[str] = mapped_column(String(160))
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IndexBuild(Base):
    __tablename__ = "index_builds"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "version_id"], ["app.document_versions.tenant_id", "app.document_versions.id"]),
        {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    workspace: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="building")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "document_id"], ["app.documents.tenant_id", "app.documents.id"]),
        Index("jobs_claim_idx", "status", "available_at"), {"schema": "app"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID)
    version_id: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="ingest")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "app"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    actor_id: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    resource: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str] = mapped_column(String(80), default="")
    signature: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditDelivery(Base):
    __tablename__ = "audit_deliveries"
    __table_args__ = {"schema": "app"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("app.audit_events.id"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class QueryRecord(Base):
    __tablename__ = "query_records"
    __table_args__ = {"schema": "app"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    user_id: Mapped[str] = mapped_column(String(200))
    question_hash: Mapped[str] = mapped_column(String(64))
    source_ids: Mapped[list] = mapped_column(JSONB, default=list)
    profile: Mapped[str] = mapped_column(String(160))
    prompt_revision: Mapped[str] = mapped_column(String(160))
    latency_ms: Mapped[int] = mapped_column(Integer)
    abstained: Mapped[bool] = mapped_column(Boolean)
    feedback: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = {"schema": "app"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID, index=True)
    actor_id: Mapped[str] = mapped_column(String(200))
    dataset_revision: Mapped[str] = mapped_column(String(160))
    release_revision: Mapped[str] = mapped_column(String(160))
    metrics: Mapped[dict] = mapped_column(JSONB)
    cases: Mapped[list] = mapped_column(JSONB)
    passed: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
