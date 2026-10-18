import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError

from rag_platform.config import Settings, settings
from rag_platform.models import Document, now
from rag_platform.security import Access, can_read
from rag_platform.service import GroundedAnswer, validate_answer
from rag_platform.storage import file_path, validate_workspace, workspace_for


@pytest.mark.asyncio
async def test_forged_jwt_rejected():
    import jwt
    from fastapi import HTTPException

    from rag_platform.security import bearer_identity
    token = jwt.encode({"sub": "alice", "exp": 9999999999}, "fake-secret-that-is-not-an-issuer-key", algorithm="HS256")
    with pytest.raises(HTTPException) as error:
        await bearer_identity(token)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_antivirus_fails_closed(monkeypatch):
    from rag_platform.antivirus import scan
    cfg = settings()
    monkeypatch.setattr(cfg, "antivirus_host", "scanner.invalid")
    async def unavailable(*args):
        raise ConnectionError("scanner unavailable")
    monkeypatch.setattr("rag_platform.antivirus.asyncio.open_connection", unavailable)
    with pytest.raises(ConnectionError):
        await scan("does-not-need-to-exist.txt")


def test_document_acl_expiry_hold_and_tenant():
    tenant, other = uuid.uuid4(), uuid.uuid4()
    actor = Access(tenant, "bob", "viewer", ("hr",), 1)
    doc = Document(tenant_id=tenant, owner_id="alice", acl_users=[], acl_groups=[])
    assert not can_read(doc, actor)
    doc.acl_groups = ["hr"]
    assert can_read(doc, actor)
    doc.retention_until = now() - timedelta(seconds=1)
    assert not can_read(doc, actor)
    doc.legal_hold = True
    assert can_read(doc, actor)
    doc.tenant_id = other
    assert not can_read(doc, actor)


def test_workspace_rejects_cross_tenant():
    tenant, doc = uuid.uuid4(), uuid.uuid4()
    workspace = workspace_for(tenant, doc, uuid.uuid4())
    validate_workspace(workspace, tenant, doc)
    with pytest.raises(ValueError):
        validate_workspace(workspace, uuid.uuid4(), doc)


def test_path_traversal():
    with pytest.raises(ValueError):
        file_path("../../secrets.json")


@pytest.mark.parametrize("citation,answer", [
    ({"source_id": "S9", "quote": "Limit Rp500.000"}, "Limit Rp500.000 [S9]"),
    ({"source_id": "S1", "quote": "Limit Rp900.000"}, "Limit Rp900.000 [S1]"),
    ({"source_id": "S1", "quote": "Limit Rp500.000"}, "Limit Rp500.000 [S2]"),
])
def test_fabricated_citations_fail_closed(citation, answer):
    result = GroundedAnswer(answer=answer, abstained=False, citations=[citation])
    with pytest.raises(ValueError):
        validate_answer(result, [{"source_id": "S1", "content": "Limit Rp500.000 per hari."}])


def test_valid_citation():
    answer = GroundedAnswer(answer="Limit Rp500.000 [S1]", abstained=False,
                            citations=[{"source_id": "S1", "quote": "Limit Rp500.000"}])
    assert not validate_answer(answer, [{"source_id": "S1", "content": "Limit Rp500.000 per hari."}]).abstained


def test_embedding_artifact_change_requires_reindex():
    from rag_platform.rag_process import validate_embedding_configuration
    cfg = settings().model_copy(update={"embedding_digest": "a" * 64})
    snapshot = {"embedding_model": cfg.embedding_model, "embedding_dim": cfg.embedding_dim, "embedding_digest": "b" * 64}
    with pytest.raises(ValueError, match="reindex"):
        validate_embedding_configuration(snapshot, cfg)
    snapshot["embedding_digest"] = cfg.embedding_digest
    validate_embedding_configuration(snapshot, cfg)


def test_production_forbids_dev_auth():
    values = settings().model_dump()
    values.update(app_env="production", dev_auth_enabled=True)
    with pytest.raises(ValidationError):
        Settings(**values)
