import asyncio
import json
import logging
import os
import signal
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .antivirus import scan
from .config import settings
from .db import engine, transaction, verify_runtime_role
from .metrics import worker_metrics
from .models import Audit, AuditDelivery, Document, DocumentVersion, IndexBuild, Job, now
from .observability import AUDIT_PENDING, BACKLOG, JOBS, WORKER_HEARTBEAT, configure_logging
from .runtime import run_process
from .security import Access, audit
from .service import RagService
from .storage import file_path, workspace_for

log = logging.getLogger(__name__)


def claim():
    with Session(engine(worker=True)) as session, session.begin():
        result = session.execute(text("SELECT * FROM app.claim_job(:lease,:attempts)"),
                                 {"lease": settings().job_lease_seconds, "attempts": settings().job_max_attempts}).first()
        return tuple(result) if result else None


def owned(session, job_id, token):
    job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    return job if job and job.status == "processing" and job.lease_token == token and job.lease_until > now() else None


async def heartbeat(tenant, job_id, token):
    while True:
        await asyncio.sleep(settings().job_lease_seconds / 3)
        with transaction(tenant, worker=True) as session:
            job = owned(session, job_id, token)
            if not job:
                raise RuntimeError("Job lease lost")
            job.lease_until = now() + timedelta(seconds=settings().job_lease_seconds)


def publish(tenant, job_id, token, build_id):
    """Compare-and-swap publication: an expired attempt can never publish its own workspace."""
    with transaction(tenant, worker=True) as session:
        job = owned(session, job_id, token)
        build = session.get(IndexBuild, build_id)
        if not job:
            if build and build.status == "building":
                build.status = "abandoned"
            return False
        if not build or build.version_id != job.version_id or build.status != "building":
            return False
        doc = session.scalar(select(Document).where(Document.id == job.document_id).with_for_update())
        if doc.deleted_at or doc.active_version_id != job.version_id:
            job.status, build.status = "cancelled", "abandoned"
            return False
        version = session.get(DocumentVersion, job.version_id)
        version.workspace, version.status = build.workspace, "ready"
        doc.status, job.status, build.status = "ready", "done", "published"
        job.lease_token, job.lease_until = None, None
        audit(session, Access(tenant, "worker", "system", (), 0), "document.indexed", str(doc.id),
              {"version_id": str(version.id), "workspace": build.workspace, "profile": version.profile})
        return True


async def process(tenant, job_id, token):
    service = RagService()
    with transaction(tenant, worker=True) as session:
        job = owned(session, job_id, token)
        if not job:
            return
        doc = session.get(Document, job.document_id)
        kind, document_id = job.kind, doc.id
        if kind == "purge":
            if doc.legal_hold:
                job.status = "cancelled"
                return
            if not doc.deleted_at:
                job.status = "cancelled"
                return
            versions = session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)).all()
            builds = session.scalars(select(IndexBuild).where(IndexBuild.version_id.in_([v.id for v in versions]))).all()
            # A crashed attempt may still be running in the isolated runtime. Wait for its hard timeout.
            active_ingestion = session.scalar(select(Job).where(Job.document_id == doc.id, Job.kind == "ingest", Job.status == "processing", Job.lease_until > now()).limit(1))
            if active_ingestion or any(b.status == "building" and b.created_at > now() - timedelta(seconds=settings().runtime_timeout + 600) for b in builds):
                raise RuntimeError("WaitingForBuildTimeout")
            payloads = [{"operation": "purge", "tenant_id": str(tenant), "document_id": str(doc.id), "workspace": b.workspace} for b in builds if b.status != "purged"]
            paths = {v.relative_path for v in versions}
        else:
            if doc.deleted_at or doc.active_version_id != job.version_id:
                job.status = "cancelled"
                return
            version = session.get(DocumentVersion, job.version_id)
            if version.status == "ready":
                job.status = "done"
                return
            doc.status, version.status = "processing", "processing"
            build_id = uuid.uuid4()
            workspace = workspace_for(tenant, doc.id, build_id)
            session.add(IndexBuild(id=build_id, tenant_id=tenant, version_id=version.id, workspace=workspace))
            relative = version.relative_path
            payload = {"operation": "ingest", "tenant_id": str(tenant), "document_id": str(doc.id),
                       "version_id": str(version.id), "workspace": workspace, "configuration": version.configuration,
                       "track_id": str(job.id)}
    if kind == "purge":
        for payload in payloads:
            await service.operate(payload)
        with transaction(tenant, worker=True) as session:
            job = owned(session, job_id, token)
            doc = session.get(Document, document_id)
            if not job or doc.legal_hold:
                raise RuntimeError("Purge state changed")
            for relative in paths:
                file_path(relative).unlink(missing_ok=True)
            for build in session.scalars(select(IndexBuild).where(IndexBuild.workspace.in_([p["workspace"] for p in payloads]))):
                build.status = "purged"
            for version in session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)):
                version.status, version.workspace = "purged", None
            doc.status, job.status = "deleted", "done"
            job.lease_token, job.lease_until = None, None
            audit(session, Access(tenant, "worker", "system", (), 0), "document.purged", str(doc.id))
    else:
        try:
            await scan(relative)
            import hashlib
            with transaction(tenant, worker=True) as session:
                expected = session.get(DocumentVersion, uuid.UUID(payload["version_id"])).checksum
            if hashlib.sha256(file_path(relative).read_bytes()).hexdigest() != expected:
                raise ValueError("Source checksum mismatch")
            parsed = await run_process({"operation": "parse", "relative_path": relative}, timeout=60)
            payload["text"] = parsed["text"]
            await service.operate(payload)
        except (ValueError, RuntimeError, TimeoutError, httpx.HTTPStatusError):
            # A completed runtime response or killed parser proves this attempt cannot write again.
            # Network loss/cancellation remains 'building' until the conservative hard-timeout GC.
            with transaction(tenant, worker=True) as session:
                build = session.get(IndexBuild, build_id)
                if build and build.status == "building":
                    build.status = "abandoned"
            raise
        publish(tenant, job_id, token, build_id)


def failure(tenant, job_id, token, error):
    with transaction(tenant, worker=True) as session:
        job = owned(session, job_id, token)
        if not job:
            return
        job.error = type(error).__name__  # Never save document text, SQL, or provider responses.
        final = job.attempts >= settings().job_max_attempts
        job.status = "dead_letter" if final else "retry_wait"
        job.available_at = now() + timedelta(seconds=min(300, 10 * 2 ** (job.attempts - 1)))
        job.lease_until, job.lease_token = None, None
        doc = session.get(Document, job.document_id)
        if job.kind == "ingest" and doc.active_version_id == job.version_id and not doc.deleted_at:
            doc.status = "failed" if final else "queued"
        JOBS.labels("failed" if final else "retry").inc()


def tenant_ids():
    return [uuid.UUID(value) for value in json.loads(settings().tenant_stores_file.read_text())]


async def maintenance():
    """Retention, orphan cleanup and durable audit outbox, each under an explicit tenant scope."""
    cfg = settings()
    counts, audit_count = {}, 0
    delivery_unavailable = False
    with Session(engine(worker=True)) as session, session.begin():
        session.execute(text("SELECT app.prune_expired_sessions()"))
    for tenant in tenant_ids():
        with transaction(tenant, worker=True) as session:
            from sqlalchemy import func
            for status, count in session.execute(select(Job.status, func.count()).group_by(Job.status)):
                counts[status] = counts.get(status, 0) + count
            audit_count += session.scalar(select(func.count()).select_from(AuditDelivery).where(AuditDelivery.delivered_at.is_(None)))
            expired = session.scalars(select(Document).where(Document.deleted_at.is_(None), Document.legal_hold.is_(False), Document.retention_until <= now()).with_for_update(skip_locked=True)).all()
            for doc in expired:
                doc.deleted_at, doc.status = now(), "deleting"
                doc.acl_revision += 1
                session.add(Job(tenant_id=tenant, document_id=doc.id, kind="purge"))
                audit(session, Access(tenant, "worker", "system", (), 0), "document.retention_expired", str(doc.id))
            pending = session.execute(select(AuditDelivery, Audit).join(Audit, Audit.id == AuditDelivery.id).where(AuditDelivery.delivered_at.is_(None)).limit(100).with_for_update(skip_locked=True, of=AuditDelivery)).all()
            directory = cfg.audit_export_dir / str(tenant)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            for delivery, event in pending:
                body = {"id": str(event.id), "tenant_id": str(tenant), "actor_id": event.actor_id, "action": event.action,
                        "resource": event.resource, "detail": event.detail, "request_id": event.request_id,
                        "created_at": event.created_at.isoformat(), "signature": event.signature}
                target = directory / f"{event.id}.json"
                if not target.exists():
                    temporary = target.with_suffix(".tmp")
                    with os.fdopen(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "w") as output:
                        json.dump(body, output, ensure_ascii=False, sort_keys=True)
                        output.flush()
                        os.fsync(output.fileno())
                    temporary.replace(target)
                    directory_fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                if cfg.audit_sink_url and delivery_unavailable:
                    continue
                delivery.attempts += 1
                if cfg.audit_sink_url:
                    try:
                        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                            response = await client.post(cfg.audit_sink_url, json=body, headers={"Authorization": "Bearer " + cfg.audit_sink_token.get_secret_value(), "Idempotency-Key": str(event.id)})
                            response.raise_for_status()
                    except httpx.HTTPError:
                        # Bound sink-outage cost to one timeout per cycle; keep exporting locally.
                        # An unavailable SIEM must not monopolize the ingestion worker.
                        delivery_unavailable = True
                        continue
                delivery.delivered_at = now()
        # Old unpublished workspaces can contain late writes from a crashed attempt.
        with transaction(tenant, worker=True) as session:
            cutoff = now() - timedelta(seconds=cfg.runtime_timeout + 600)
            orphans = session.execute(select(IndexBuild, DocumentVersion).join(DocumentVersion, IndexBuild.version_id == DocumentVersion.id).where(
                IndexBuild.status.in_(["building", "abandoned"]), IndexBuild.created_at < cutoff).limit(10)).all()
            snapshots = []
            for b, v in orphans:
                active = session.scalar(select(Job).where(Job.version_id == v.id, Job.status == "processing", Job.lease_until > now()).limit(1))
                if not active and v.workspace != b.workspace:
                    snapshots.append((b.id, str(v.document_id), b.workspace))
        for build_id, document_id, workspace in snapshots:
            await RagService().operate({"operation": "purge", "tenant_id": str(tenant), "document_id": document_id, "workspace": workspace})
            with transaction(tenant, worker=True) as session:
                build = session.get(IndexBuild, build_id)
                if build.status in {"building", "abandoned"}:
                    build.status = "purged"
    for status in ["queued", "processing", "retry_wait", "dead_letter", "done", "cancelled"]:
        BACKLOG.labels(status).set(counts.get(status, 0))
    AUDIT_PENDING.set(audit_count)


async def execute_job(claimed):
    job_id, tenant, token = claimed
    task = asyncio.create_task(process(tenant, job_id, token))
    renewal = asyncio.create_task(heartbeat(tenant, job_id, token))
    try:
        done, _ = await asyncio.wait([task, renewal], return_when=asyncio.FIRST_COMPLETED)
        for finished in done:
            finished.result()
        JOBS.labels("completed").inc()
    except Exception as error:
        failure(tenant, job_id, token, error)
        log.error("job_failed id=%s type=%s", job_id, type(error).__name__)
    finally:
        task.cancel()
        renewal.cancel()
        await asyncio.gather(task, renewal, return_exceptions=True)


async def loop():
    configure_logging()
    verify_runtime_role(worker=True)
    metrics_server = worker_metrics()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    last_maintenance = 0
    while not stop.is_set():
        try:
            if asyncio.get_running_loop().time() - last_maintenance > 30:
                await maintenance()
                last_maintenance = asyncio.get_running_loop().time()
            claimed = claim()
            import time
            WORKER_HEARTBEAT.set(time.time())
            if claimed:
                await execute_job(claimed)
                continue
        except Exception as error:
            log.error("worker_cycle_failed type=%s", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), settings().job_poll_seconds)
        except TimeoutError:
            pass
    metrics_server.shutdown()


def main():
    asyncio.run(loop())


if __name__ == "__main__":
    main()
