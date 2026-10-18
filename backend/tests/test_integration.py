"""These tests use real PostgreSQL/RLS. Only expensive model calls are injected."""
import uuid
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from rag_platform.api import app
from rag_platform.cli import DEMO_TENANTS, admin_dsn
from rag_platform.config import settings
from rag_platform.db import transaction, verify_runtime_role
from rag_platform.models import Document, IndexBuild, Job, now
from rag_platform.service import GroundedAnswer
from rag_platform.storage import workspace_for
from rag_platform.worker import publish

pytestmark = pytest.mark.integration
TENANT = DEMO_TENANTS[0]


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value


def login(client, user="alice"):
    response = client.post("/auth/dev-login", json={"username": user, "password": settings().dev_password.get_secret_value()})
    assert response.status_code == 200
    me = client.get("/api/me").json()
    client.headers.update({"X-Tenant-ID": me["tenants"][0]["id"], "X-CSRF-Token": me["csrf"]})
    return me


@pytest.fixture
def document(client):
    login(client)
    response = client.post("/api/documents", files={"file": ("test-policy.txt", b"Limit Rp500.000 per hari.", "text/plain")})
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    with psycopg.connect(admin_dsn()) as connection:
        row = connection.execute("SELECT active_version_id FROM app.documents WHERE id=%s", [identifier]).fetchone()
        version = row[0]
        workspace = workspace_for(uuid.UUID(TENANT), uuid.UUID(identifier), uuid.uuid4())
        connection.execute("UPDATE app.documents SET status='ready' WHERE id=%s", [identifier])
        connection.execute("UPDATE app.document_versions SET status='ready',workspace=%s WHERE id=%s", [workspace, version])
    yield identifier
    # Leave append-only audit as evidence; remove temporary test sources and queue rows.
    with psycopg.connect(admin_dsn()) as connection:
        paths = connection.execute("SELECT relative_path FROM app.document_versions WHERE document_id=%s", [identifier]).fetchall()
        connection.execute("DELETE FROM app.index_builds WHERE version_id IN (SELECT id FROM app.document_versions WHERE document_id=%s)", [identifier])
        connection.execute("DELETE FROM app.jobs WHERE document_id=%s", [identifier])
        connection.execute("DELETE FROM app.document_versions WHERE document_id=%s", [identifier])
        connection.execute("DELETE FROM app.documents WHERE id=%s", [identifier])
    from rag_platform.storage import file_path
    for (path,) in paths:
        file_path(path).unlink(missing_ok=True)


class Model:
    def __init__(self, hook=None):
        self.hook = hook

    async def retrieve(self, question, snapshots, parameters):
        if not snapshots:
            return []
        return [{"source_id": "S1", "document_id": snapshots[0]["document_id"],
                 "version_id": snapshots[0]["version_id"], "title": snapshots[0]["title"], "content": "Limit Rp500.000 per hari."}]

    async def generate(self, question, sources):
        if self.hook:
            self.hook()
        if not sources:
            return GroundedAnswer(answer="", abstained=True, citations=[])
        return GroundedAnswer(answer="Limit Rp500.000 per hari [S1]", abstained=False,
                              citations=[{"source_id": "S1", "quote": "Limit Rp500.000 per hari."}])


def test_runtime_roles_no_bypass():
    verify_runtime_role()
    verify_runtime_role(worker=True)


def test_rls_no_scope_and_other_tenant(document):
    with transaction() as session:
        assert session.get(Document, uuid.UUID(document)) is None
    with transaction(DEMO_TENANTS[1]) as session:
        assert session.get(Document, uuid.UUID(document)) is None


def test_csrf_and_viewer_upload(client):
    login(client, "bob")
    response = client.post("/api/documents", files={"file": ("x.txt", b"Test")})
    assert response.status_code == 403
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/chat", json={"question": "hello"}).status_code == 403


def test_private_document_and_cross_tenant(client, document):
    app.state.rag = Model()
    login(client, "bob")
    assert document not in [d["id"] for d in client.get("/api/documents").json()]
    assert client.post("/api/chat", json={"question": "limit?", "document_ids": [document]}).status_code == 404
    login(client, "mallory")
    assert client.get(f"/api/documents/{document}/download").status_code == 404


def test_permission_revocation_during_generation(client, document):
    login(client, "alice")
    bob = "22222222-2222-4222-8222-222222222222"
    assert client.put(f"/api/documents/{document}/permissions", json={"users": [bob], "expected_revision": 1}).status_code == 200
    login(client, "bob")

    def revoke():
        with psycopg.connect(admin_dsn()) as connection:
            connection.execute("UPDATE app.documents SET acl_users='[]',acl_revision=acl_revision+1 WHERE id=%s", [document])

    app.state.rag = Model(revoke)
    result = client.post("/api/chat", json={"question": "limit?", "document_ids": [document]})
    assert result.status_code == 409, result.text
    assert "Rp500.000" not in result.text


def test_answer_source_and_feedback(client, document):
    app.state.rag = Model()
    result = client.post("/api/chat", json={"question": "limit?", "document_ids": [document]})
    assert result.status_code == 200, result.text
    assert result.json()["sources"][0]["document_id"] == document
    assert "content" not in result.json()["sources"][0]
    assert client.post(f"/api/queries/{result.json()['query_id']}/feedback", json={"rating": "helpful"}).status_code == 200


def test_delete_blocks_read_immediately(client, document):
    assert client.delete(f"/api/documents/{document}").status_code == 202
    assert client.get(f"/api/documents/{document}/download").status_code == 404


def test_audit_cannot_be_mutated(client, document):
    rows = client.get("/api/audit").json()
    assert rows
    with pytest.raises(ProgrammingError), transaction(TENANT) as session:
        session.execute(text("DELETE FROM app.audit_events WHERE id=:id"), {"id": rows[0]["id"]})


def test_stale_worker_cannot_publish(document):
    tenant, doc_id = uuid.UUID(TENANT), uuid.UUID(document)
    build_id, current, stale = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with transaction(tenant, worker=True) as session:
        doc = session.get(Document, doc_id)
        job = session.scalar(select(Job).where(Job.document_id == doc.id))
        job.status, job.lease_token, job.lease_until = "processing", current, now() + timedelta(seconds=90)
        job_id = job.id
        session.add(IndexBuild(id=build_id, tenant_id=tenant, version_id=doc.active_version_id,
                               workspace=workspace_for(tenant, doc_id, build_id)))
    assert not publish(tenant, job_id, stale, build_id)
    with transaction(tenant) as session:
        assert session.get(IndexBuild, build_id).status == "abandoned"


def test_repeat_publication_does_not_abandon_live_index(document):
    tenant, doc_id = uuid.UUID(TENANT), uuid.UUID(document)
    build_id, token = uuid.uuid4(), uuid.uuid4()
    with transaction(tenant, worker=True) as session:
        doc = session.get(Document, doc_id)
        job = session.scalar(select(Job).where(Job.document_id == doc.id))
        job.status, job.lease_token, job.lease_until = "processing", token, now() + timedelta(seconds=90)
        job_id = job.id
        session.add(IndexBuild(id=build_id, tenant_id=tenant, version_id=doc.active_version_id,
                               workspace=workspace_for(tenant, doc_id, build_id)))
    assert publish(tenant, job_id, token, build_id)
    assert not publish(tenant, job_id, token, build_id)
    with transaction(tenant) as session:
        assert session.get(IndexBuild, build_id).status == "published"


def test_version_replacement_preserves_acl(client, document):
    response = client.post(f"/api/documents/{document}/versions", files={"file": ("updated.txt", b"Updated policy.")})
    assert response.status_code == 202
    versions = client.get(f"/api/documents/{document}/versions").json()
    assert len(versions) == 2
    assert client.post(f"/api/documents/{document}/versions", files={"file": ("another.txt", b"Next")}).status_code == 409


def test_legal_hold_blocks_deletion(client, document):
    assert client.put(f"/api/documents/{document}/permissions", json={"users": [], "expected_revision": 1, "legal_hold": True}).status_code == 200
    assert client.delete(f"/api/documents/{document}").status_code == 409


def test_foreign_origin_csrf(client):
    login(client)
    assert client.post("/api/chat", headers={"Origin": "https://evil.invalid"}, json={"question": "hello"}).status_code == 403


def test_oversized_request_stopped_before_parsing(client):
    response = client.post("/api/documents", headers={"Content-Length": str(100 * 1024 * 1024)}, content=b"x")
    assert response.status_code == 413


def test_concurrent_job_claim_and_expired_lease(document):
    from concurrent.futures import ThreadPoolExecutor

    from rag_platform.worker import claim
    tenant = uuid.UUID(TENANT)
    with transaction(tenant, worker=True) as session:
        original = session.scalar(select(Job).where(Job.document_id == uuid.UUID(document)))
        second = Job(tenant_id=tenant, document_id=original.document_id, version_id=original.version_id)
        session.add(second)
        session.flush()
        expected = {original.id, second.id}
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert {c[0] for c in claims} == expected
    first = claims[0]
    with transaction(tenant, worker=True) as session:
        job = session.get(Job, first[0])
        job.lease_until = now() - timedelta(seconds=1)
    reclaimed = claim()
    assert reclaimed[0] == first[0] and reclaimed[2] != first[2]


def test_tenant_database_credentials_cannot_read_app():
    from rag_platform.rag_process import tenant_store
    store = tenant_store(TENANT)
    store["dbname"] = "enterprise"
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(**store)


@pytest.mark.asyncio
async def test_completed_parser_failure_marks_attempt_abandoned(document, monkeypatch):
    from rag_platform.models import DocumentVersion
    from rag_platform.worker import process
    tenant, token = uuid.UUID(TENANT), uuid.uuid4()
    with transaction(tenant, worker=True) as session:
        doc = session.get(Document, uuid.UUID(document))
        doc.status = "queued"
        version_id = doc.active_version_id
        session.get(DocumentVersion, doc.active_version_id).status = "queued"
        job = session.scalar(select(Job).where(Job.document_id == doc.id))
        job.status, job.lease_token, job.lease_until = "processing", token, now() + timedelta(seconds=90)
        job_id = job.id
    async def parse_fails(*args, **kwargs):
        raise RuntimeError("Parser process completed with error")
    monkeypatch.setattr("rag_platform.worker.run_process", parse_fails)
    with pytest.raises(RuntimeError):
        await process(tenant, job_id, token)
    with transaction(tenant) as session:
        build = session.scalar(select(IndexBuild).where(IndexBuild.version_id == version_id))
        assert build.status == "abandoned"
