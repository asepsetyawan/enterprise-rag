import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from starlette.staticfiles import StaticFiles

from .cli import DEMO_USERS
from .config import settings
from .db import transaction, verify_runtime_role
from .models import (
    Audit,
    Document,
    DocumentVersion,
    EvaluationRun,
    Job,
    LoginSession,
    Membership,
    QueryRecord,
    Tenant,
    User,
    now,
)
from .observability import QUERIES, BodyLimit, RequestTelemetry, configure_logging
from .security import (
    Access,
    Identity,
    access_for,
    audit,
    bearer_identity,
    can_manage,
    can_read,
    digest,
    identity,
    internal_oidc_url,
    new_session,
    require_read,
)
from .service import GroundedAnswer, InvalidGroundedAnswer, RagService, validate_answer
from .storage import file_path, save_upload


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    verify_runtime_role()
    app.state.rag = RagService()
    app.state.query_slots = asyncio.Semaphore(8)
    yield


app = FastAPI(title="CNF Enterprise Knowledge", version="0.1.0", lifespan=lifespan,
              docs_url=None if settings().app_env == "production" else "/api/docs", redoc_url=None)
app.add_middleware(RequestTelemetry)
app.add_middleware(BodyLimit)


@app.exception_handler(Exception)
async def unexpected(request, error):
    # Deliberately exclude exception messages: SQL/provider errors can contain sensitive input.
    import logging
    logging.getLogger(__name__).error("request_failed type=%s request_id=%s", type(error).__name__, getattr(request.state, "request_id", ""))
    return JSONResponse({"detail": "Request failed", "request_id": getattr(request.state, "request_id", "")}, status_code=500)


async def access(request: Request, who: Identity = Depends(identity)):
    return access_for(who, request.headers.get("X-Tenant-ID"))


def require_role(actor, *roles):
    if actor.role not in roles:
        raise HTTPException(403, "Role does not permit this operation")


def configuration():
    cfg = settings()
    return {"chunk_size": cfg.chunk_size, "chunk_overlap": cfg.chunk_overlap,
            "embedding_model": cfg.embedding_model, "embedding_dim": cfg.embedding_dim,
            "embedding_digest": cfg.embedding_digest, "extraction_model": cfg.llm_model,
            "extraction_model_digest": cfg.llm_digest}


def document_json(row):
    return {"id": str(row.id), "title": row.title, "filename": row.filename, "status": row.status,
            "owner_id": row.owner_id, "active_version_id": str(row.active_version_id) if row.active_version_id else None,
            "acl_users": row.acl_users, "acl_groups": row.acl_groups, "acl_revision": row.acl_revision,
            "retention_until": row.retention_until, "legal_hold": row.legal_hold, "created_at": row.created_at}


def cookie(response, token):
    response.set_cookie(settings().cookie_name, token, httponly=True, secure=settings().app_env == "production",
                        samesite="lax", max_age=settings().session_hours * 3600, path="/")
    return response


@app.get("/auth/login")
async def login():
    cfg = settings()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = __import__("base64").urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    values = {"client_id": cfg.oidc_client_id, "redirect_uri": cfg.public_origin + "/auth/callback", "response_type": "code",
              "scope": "openid profile email", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    if cfg.oidc_required_acr:
        values["acr_values"] = cfg.oidc_required_acr
    response = RedirectResponse(cfg.oidc_issuer + "/protocol/openid-connect/auth?" + urlencode(values))
    signed = URLSafeTimedSerializer(cfg.session_secret.get_secret_value(), salt="oidc-state").dumps(
        {"state": state, "nonce": nonce, "verifier": verifier})
    response.set_cookie("rag_oidc", signed, max_age=300, httponly=True, secure=cfg.app_env == "production", samesite="lax", path="/auth")
    return response


@app.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    cfg = settings()
    try:
        stored = URLSafeTimedSerializer(cfg.session_secret.get_secret_value(), salt="oidc-state").loads(request.cookies.get("rag_oidc", ""), max_age=300)
        if not code or not hmac.compare_digest(state, stored["state"]):
            raise ValueError("Invalid state")
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(internal_oidc_url(cfg.oidc_issuer + "/protocol/openid-connect/token"), data={
                "grant_type": "authorization_code", "code": code, "client_id": cfg.oidc_client_id,
                "client_secret": cfg.oidc_client_secret.get_secret_value(), "redirect_uri": cfg.public_origin + "/auth/callback",
                "code_verifier": stored["verifier"]})
            response.raise_for_status()
            tokens = response.json()
        # Access token is verified independently. ID token uses the same trusted issuer/JWKS/audience.
        who = await bearer_identity(tokens["access_token"])
        id_who = await bearer_identity(tokens["id_token"])
        from .security import _jwks
        token = tokens["id_token"]
        header = jwt.get_unverified_header(token)
        key = next(key for key in _jwks["data"]["keys"] if key["kid"] == header["kid"])
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"], audience=cfg.oidc_client_id, issuer=cfg.oidc_issuer)
        if who.user_id != id_who.user_id or not hmac.compare_digest(claims.get("nonce", ""), stored["nonce"]):
            raise ValueError("Invalid nonce or subject")
        session_token = new_session(who.user_id, claims.get("name", who.user_id), claims.get("email", ""))
    except (BadSignature, ValueError, KeyError, StopIteration, httpx.HTTPError, jwt.PyJWTError, HTTPException) as exc:
        raise HTTPException(401, "SSO authentication failed") from exc
    response = cookie(RedirectResponse("/"), session_token)
    response.delete_cookie("rag_oidc", path="/auth")
    return response


class DevLogin(BaseModel):
    username: str
    password: str


@app.post("/auth/dev-login")
async def dev_login(body: DevLogin, request: Request):
    cfg = settings()
    if cfg.app_env != "development" or not cfg.dev_auth_enabled:
        raise HTTPException(404)
    if request.headers.get("Origin", cfg.public_origin) != cfg.public_origin:
        raise HTTPException(403, "Invalid origin")
    if body.username not in DEMO_USERS or not hmac.compare_digest(body.password, cfg.dev_password.get_secret_value()):
        raise HTTPException(401, "Invalid credentials")
    return cookie(JSONResponse({"ok": True}), new_session(DEMO_USERS[body.username], body.username.title()))


@app.post("/auth/logout")
async def logout(request: Request, who: Identity = Depends(identity)):
    hashed = digest(request.cookies.get(settings().cookie_name, ""))
    with transaction(session_hash=hashed) as session:
        row = session.get(LoginSession, hashed)
        if row:
            session.delete(row)
    response = JSONResponse({"ok": True})
    response.delete_cookie(settings().cookie_name, path="/")
    return response


@app.get("/api/me")
async def me(who: Identity = Depends(identity)):
    with transaction(user=who.user_id) as session:
        user = session.get(User, who.user_id)
        if not user or not user.active:
            raise HTTPException(403, "Account disabled")
        memberships = session.scalars(select(Membership).where(Membership.user_id == who.user_id, Membership.active)).all()
        tenants = [{"id": str(m.tenant_id), "name": session.get(Tenant, m.tenant_id).name, "role": m.role} for m in memberships]
        return {"user_id": who.user_id, "name": user.name, "csrf": who.csrf, "tenants": tenants}


@app.get("/api/members")
async def members(actor: Access = Depends(access)):
    require_role(actor, "tenant_admin", "editor")
    with transaction(actor.tenant_id, actor.user_id) as session:
        rows = session.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.tenant_id == actor.tenant_id, Membership.active)).all()
        return [{"user_id": m.user_id, "name": u.name, "role": m.role, "groups": m.groups} for m, u in rows]


@app.get("/api/documents")
async def documents(actor: Access = Depends(access)):
    with transaction(actor.tenant_id, actor.user_id) as session:
        return [document_json(d) for d in session.scalars(select(Document).order_by(Document.created_at.desc())) if can_read(d, actor)]


@app.post("/api/documents", status_code=202)
async def upload(request: Request, file: UploadFile = File(...), actor: Access = Depends(access)):
    require_role(actor, "editor", "tenant_admin")
    document_id, version_id = uuid.uuid4(), uuid.uuid4()
    try:
        filename, relative, checksum, size = await save_upload(file, actor.tenant_id, document_id, version_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        with transaction(actor.tenant_id, actor.user_id) as session:
            doc = Document(id=document_id, tenant_id=actor.tenant_id, owner_id=actor.user_id, title=filename,
                           filename=filename, active_version_id=version_id, acl_users=[], acl_groups=[])
            doc.retention_until = now() + timedelta(days=settings().default_retention_days)
            session.add(doc)
            session.flush()
            session.add(DocumentVersion(id=version_id, tenant_id=actor.tenant_id, document_id=document_id,
                                        checksum=checksum, relative_path=relative, filename=filename, bytes=size,
                                        profile=settings().retrieval_profile, configuration=configuration()))
            session.flush()
            session.add(Job(tenant_id=actor.tenant_id, document_id=document_id, version_id=version_id))
            audit(session, actor, "document.upload", str(document_id), {"version_id": str(version_id), "sha256": checksum}, request.state.request_id)
        return {"id": str(document_id), "status": "queued"}
    except BaseException:
        file_path(relative).unlink(missing_ok=True)
        raise


class PermissionChange(BaseModel):
    users: list[str] = Field(default_factory=list, max_length=200)
    groups: list[str] = Field(default_factory=list, max_length=100)
    expected_revision: int
    retention_until: datetime | None = None
    legal_hold: bool = False


@app.put("/api/documents/{document_id}/permissions")
async def permissions(document_id: uuid.UUID, body: PermissionChange, request: Request, actor: Access = Depends(access)):
    require_role(actor, "editor", "tenant_admin")
    if body.retention_until and (body.retention_until.tzinfo is None or body.retention_until <= now()):
        raise HTTPException(422, "Retention must be a future timestamp with timezone")
    if settings().app_env == "production" and not body.retention_until and not body.legal_hold:
        raise HTTPException(422, "Production documents require retention or legal hold")
    with transaction(actor.tenant_id, actor.user_id) as session:
        doc = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if not doc or doc.deleted_at or not can_manage(doc, actor):
            raise HTTPException(404, "Document not found")
        if body.expected_revision != doc.acl_revision:
            raise HTTPException(409, "Permissions changed; reload and try again")
        if actor.role != "tenant_admin" and (doc.legal_hold != body.legal_hold or doc.retention_until != body.retention_until):
            raise HTTPException(403, "Retention and legal hold require tenant admin")
        memberships = session.scalars(select(Membership).where(Membership.tenant_id == actor.tenant_id, Membership.active)).all()
        valid_users = {m.user_id for m in memberships}
        valid_groups = {g for m in memberships for g in m.groups} | {"all"}
        if not set(body.users) <= valid_users or not set(body.groups) <= valid_groups:
            raise HTTPException(422, "Unknown tenant user or group")
        doc.acl_users, doc.acl_groups = sorted(set(body.users)), sorted(set(body.groups))
        doc.retention_until, doc.legal_hold = body.retention_until, body.legal_hold
        doc.acl_revision += 1
        audit(session, actor, "document.permissions", str(document_id), body.model_dump(mode="json"), request.state.request_id)
        return document_json(doc)


@app.post("/api/documents/{document_id}/reindex", status_code=202)
async def reindex(document_id: uuid.UUID, actor: Access = Depends(access)):
    require_role(actor, "editor", "tenant_admin")
    with transaction(actor.tenant_id, actor.user_id) as session:
        doc = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if not doc or not can_manage(doc, actor) or doc.deleted_at:
            raise HTTPException(404)
        if doc.status in {"queued", "processing"}:
            raise HTTPException(409, "An index build is already pending")
        original = session.get(DocumentVersion, doc.active_version_id)
        version = DocumentVersion(tenant_id=actor.tenant_id, document_id=doc.id, checksum=original.checksum,
                                  relative_path=original.relative_path, filename=original.filename, bytes=original.bytes,
                                  configuration=configuration(), profile=settings().retrieval_profile)
        session.add(version)
        session.flush()
        doc.active_version_id, doc.status = version.id, "queued"
        session.add(Job(tenant_id=actor.tenant_id, document_id=doc.id, version_id=version.id))
        audit(session, actor, "document.reindex", str(doc.id), {"version_id": str(version.id)})
    return {"status": "queued"}


@app.post("/api/documents/{document_id}/versions", status_code=202)
async def replace_source(document_id: uuid.UUID, file: UploadFile = File(...), actor: Access = Depends(access)):
    require_role(actor, "editor", "tenant_admin")
    with transaction(actor.tenant_id, actor.user_id) as session:
        doc = session.get(Document, document_id)
        if not doc or doc.deleted_at or not can_manage(doc, actor):
            raise HTTPException(404)
        if doc.status in {"queued", "processing"}:
            raise HTTPException(409, "An index build is already pending")
    version_id = uuid.uuid4()
    filename, relative, checksum, size = await save_upload(file, actor.tenant_id, document_id, version_id)
    try:
        with transaction(actor.tenant_id, actor.user_id) as session:
            doc = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
            if not doc or doc.deleted_at or not can_manage(doc, actor) or doc.status in {"queued", "processing"}:
                raise HTTPException(409, "Document state changed")
            session.add(DocumentVersion(id=version_id, tenant_id=actor.tenant_id, document_id=document_id,
                                        filename=filename, relative_path=relative, checksum=checksum, bytes=size,
                                        profile=settings().retrieval_profile, configuration=configuration()))
            session.flush()
            doc.active_version_id, doc.status, doc.filename = version_id, "queued", filename
            session.add(Job(tenant_id=actor.tenant_id, document_id=document_id, version_id=version_id))
            audit(session, actor, "document.new_version", str(document_id), {"version_id": str(version_id), "sha256": checksum})
        return {"id": str(version_id), "status": "queued"}
    except BaseException:
        file_path(relative).unlink(missing_ok=True)
        raise


@app.get("/api/documents/{document_id}/versions")
async def versions(document_id: uuid.UUID, actor: Access = Depends(access)):
    with transaction(actor.tenant_id, actor.user_id) as session:
        require_read(session, document_id, actor)
        return [{"id": str(v.id), "filename": v.filename, "status": v.status, "profile": v.profile,
                 "checksum": v.checksum, "created_at": v.created_at} for v in session.scalars(
                    select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.created_at.desc()))]


@app.delete("/api/documents/{document_id}", status_code=202)
async def delete_document(document_id: uuid.UUID, actor: Access = Depends(access)):
    require_role(actor, "editor", "tenant_admin")
    with transaction(actor.tenant_id, actor.user_id) as session:
        doc = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if not doc or not can_manage(doc, actor):
            raise HTTPException(404)
        if doc.legal_hold:
            raise HTTPException(409, "Document is under legal hold")
        if not doc.deleted_at:
            doc.deleted_at, doc.status = now(), "deleting"
            doc.acl_revision += 1
            session.add(Job(tenant_id=actor.tenant_id, document_id=doc.id, kind="purge"))
            audit(session, actor, "document.delete_requested", str(doc.id))
    return {"status": "deleting"}


@app.get("/api/documents/{document_id}/download")
async def download(document_id: uuid.UUID, request: Request, version_id: uuid.UUID | None = None, actor: Access = Depends(access)):
    with transaction(actor.tenant_id, actor.user_id) as session:
        doc = require_read(session, document_id, actor)
        if doc.status != "ready":
            raise HTTPException(409, "Source download requires completed ingestion")
        version = session.get(DocumentVersion, version_id or doc.active_version_id)
        if not version or version.document_id != doc.id or version.status not in {"ready", "archived"}:
            raise HTTPException(404, "Source version unavailable")
        audit(session, actor, "document.download", str(doc.id), request_id=request.state.request_id)
        # Text is sent as an attachment; HTML and active formats are not accepted.
        return FileResponse(file_path(version.relative_path), filename=version.filename, media_type="application/octet-stream")


@app.get("/api/jobs")
async def jobs(actor: Access = Depends(access)):
    require_role(actor, "editor", "tenant_admin")
    with transaction(actor.tenant_id, actor.user_id) as session:
        return [{"id": str(j.id), "document_id": str(j.document_id), "kind": j.kind, "status": j.status,
                 "attempts": j.attempts, "error": j.error, "created_at": j.created_at} for j in
                session.scalars(select(Job).order_by(Job.created_at.desc()).limit(100))]


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry(job_id: uuid.UUID, actor: Access = Depends(access)):
    require_role(actor, "tenant_admin")
    with transaction(actor.tenant_id, actor.user_id) as session:
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job:
            raise HTTPException(404)
        if job.status != "dead_letter":
            raise HTTPException(409, "Only dead-letter jobs can be retried")
        doc = session.get(Document, job.document_id)
        if job.kind == "ingest" and (doc.deleted_at or doc.active_version_id != job.version_id):
            raise HTTPException(409, "Document version is no longer active")
        job.status, job.attempts, job.error, job.available_at = "queued", 0, None, now()
        if job.kind == "ingest":
            doc.status = "queued"
        audit(session, actor, "job.retry", str(job_id))
    return {"status": "queued"}


class ChatInput(BaseModel):
    question: str = Field(min_length=2, max_length=8000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    mode: str = "mix"
    top_k: int = Field(default=20, ge=1, le=100)
    chunk_top_k: int = Field(default=8, ge=1, le=30)


async def revalidate(request, tenant, snapshots):
    actor = access_for(await identity(request), tenant)
    with transaction(actor.tenant_id, actor.user_id) as session:
        for item in snapshots:
            doc = session.get(Document, uuid.UUID(item["document_id"]))
            if (not doc or not can_read(doc, actor) or doc.acl_revision != item["acl_revision"]
                    or str(doc.active_version_id) != item["version_id"] or doc.status != "ready"):
                raise HTTPException(409, "Document access or version changed; please repeat the query")
    return actor


@app.post("/api/chat")
async def chat(body: ChatInput, request: Request, actor: Access = Depends(access)):
    if body.mode not in {"mix", "naive", "local", "global", "hybrid"}:
        raise HTTPException(422, "Invalid retrieval mode")
    started = time.monotonic()
    with transaction(actor.tenant_id, actor.user_id) as session:
        query = select(Document).where(Document.status == "ready").order_by(Document.id)
        if body.document_ids:
            query = query.where(Document.id.in_(body.document_ids))
        docs = [doc for doc in session.scalars(query) if can_read(doc, actor)]
        if body.document_ids and {doc.id for doc in docs} != set(body.document_ids):
            raise HTTPException(404, "One or more documents are unavailable")
        if len(docs) > settings().max_query_documents:
            raise HTTPException(422, "Select fewer documents for this query")
        snapshots = []
        for doc in docs:
            version = session.get(DocumentVersion, doc.active_version_id)
            if version and version.workspace and version.status == "ready":
                snapshots.append({"tenant_id": str(actor.tenant_id), "document_id": str(doc.id), "version_id": str(version.id),
                                  "workspace": version.workspace, "configuration": version.configuration,
                                  "title": doc.title, "acl_revision": doc.acl_revision})
    try:
        await asyncio.wait_for(request.app.state.query_slots.acquire(), 1)
    except TimeoutError as exc:
        raise HTTPException(429, "Query capacity reached", headers={"Retry-After": "5"}) from exc
    try:
        async with asyncio.timeout(settings().query_timeout):
            sources = await request.app.state.rag.retrieve(body.question, snapshots, {"mode": body.mode, "top_k": body.top_k, "chunk_top_k": body.chunk_top_k})
            for attempt in range(2):
                await revalidate(request, actor.tenant_id, snapshots)
                try:
                    answer = await request.app.state.rag.generate(body.question, sources)
                    break
                except InvalidGroundedAnswer:
                    if attempt == 1:
                        answer = GroundedAnswer(answer="", abstained=True, citations=[])
            answer = validate_answer(answer, sources)
            actor = await revalidate(request, actor.tenant_id, snapshots)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise HTTPException(503, "Retrieval or model service unavailable; try again later") from exc
    finally:
        request.app.state.query_slots.release()
    query_id = uuid.uuid4()
    source_ids = sorted({s["document_id"] for s in sources if s["source_id"] in {c.source_id for c in answer.citations}})
    with transaction(actor.tenant_id, actor.user_id) as session:
        session.add(QueryRecord(id=query_id, tenant_id=actor.tenant_id, user_id=actor.user_id,
                                question_hash=hmac.new(settings().audit_hmac_key.get_secret_value().encode(), ("question:" + body.question).encode(), hashlib.sha256).hexdigest(), source_ids=source_ids, profile=settings().retrieval_profile,
                                prompt_revision=settings().prompt_revision, latency_ms=int((time.monotonic() - started) * 1000), abstained=answer.abstained))
        audit(session, actor, "query.answer", str(query_id), {"source_ids": source_ids, "abstained": answer.abstained}, request.state.request_id)
    QUERIES.labels("abstained" if answer.abstained else "answered").inc()
    cited = {c.source_id for c in answer.citations}
    return {"query_id": str(query_id), **answer.model_dump(), "sources": [{k: v for k, v in s.items() if k != "content"} for s in sources if s["source_id"] in cited]}


class Feedback(BaseModel):
    rating: str = Field(pattern="^(helpful|incorrect|unsupported)$")


@app.post("/api/queries/{query_id}/feedback")
async def feedback(query_id: uuid.UUID, body: Feedback, actor: Access = Depends(access)):
    with transaction(actor.tenant_id, actor.user_id) as session:
        record = session.get(QueryRecord, query_id)
        if not record or record.user_id != actor.user_id:
            raise HTTPException(404)
        record.feedback = body.rating
    return {"ok": True}


@app.get("/api/audit")
async def audit_events(actor: Access = Depends(access)):
    require_role(actor, "tenant_admin", "auditor")
    with transaction(actor.tenant_id, actor.user_id) as session:
        return [{"id": str(r.id), "actor_id": r.actor_id, "action": r.action, "resource": r.resource,
                 "detail": r.detail, "created_at": r.created_at, "signature": r.signature} for r in
                session.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(200))]


class EvaluationInput(BaseModel):
    dataset_revision: str = Field(max_length=160)
    release_revision: str = Field(max_length=160)
    metrics: dict
    cases: list = Field(max_length=10000)
    passed: bool


@app.post("/api/evaluations", status_code=201)
async def record_evaluation(body: EvaluationInput, actor: Access = Depends(access)):
    require_role(actor, "tenant_admin")
    with transaction(actor.tenant_id, actor.user_id) as session:
        run = EvaluationRun(tenant_id=actor.tenant_id, actor_id=actor.user_id, **body.model_dump())
        session.add(run)
        session.flush()
        audit(session, actor, "evaluation.record", str(run.id), {"passed": run.passed, "release_revision": run.release_revision})
        return {"id": str(run.id)}


@app.get("/api/evaluations")
async def evaluations(actor: Access = Depends(access)):
    require_role(actor, "tenant_admin", "auditor")
    with transaction(actor.tenant_id, actor.user_id) as session:
        return [{"id": str(r.id), "dataset_revision": r.dataset_revision, "release_revision": r.release_revision,
                 "metrics": r.metrics, "passed": r.passed, "created_at": r.created_at} for r in
                session.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(50))]


@app.get("/health/live")
async def live():
    return {"status": "up"}


@app.get("/health/ready")
async def ready():
    try:
        with transaction() as session:
            session.execute(text("SELECT 1"))
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            results = await asyncio.gather(client.get(settings().runtime_url + "/health"), client.get(settings().gateway_url + "/health"))
            for result in results:
                result.raise_for_status()
    except Exception as exc:
        raise HTTPException(503, "Dependencies unavailable") from exc
    return {"status": "ready"}


@app.get("/metrics")
async def metrics(request: Request):
    if not settings().metrics_token.get_secret_value() or not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + settings().metrics_token.get_secret_value()):
        raise HTTPException(401)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if settings().frontend_dir.exists():
    app.mount("/", StaticFiles(directory=settings().frontend_dir, html=True), name="portal")
