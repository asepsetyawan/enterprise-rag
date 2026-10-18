"""Privileged, offline provisioning. Never mount its database credential in the API."""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import secrets
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Document, Job, LoginSession, Membership, Tenant, User, now
from .security import Access, audit

DEMO_TENANTS = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
DEMO_USERS = {"alice": "11111111-1111-4111-8111-111111111111", "bob": "22222222-2222-4222-8222-222222222222", "mallory": "33333333-3333-4333-8333-333333333333"}


def admin_dsn():
    value = settings().migration_database_url
    if not value:
        raise RuntimeError("MIGRATION_DATABASE_URL required for offline administration")
    return value.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


def provision_store(tenant):
    cfg = settings()
    if cfg.app_env == "production" and psycopg.conninfo.conninfo_to_dict(admin_dsn()).get("sslmode") != "verify-full":
        raise RuntimeError("Production provisioning requires sslmode=verify-full")
    tenant = uuid.UUID(str(tenant))
    path = cfg.tenant_stores_file
    stores = json.loads(path.read_text()) if path.exists() else {}
    if str(tenant) in stores:
        return
    role = "rag_t_" + tenant.hex
    database = "rag_" + tenant.hex
    password = secrets.token_hex(32)
    with psycopg.connect(admin_dsn(), autocommit=True) as connection:
        info = connection.info
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", [role]).fetchone():
            raise RuntimeError("Tenant role exists without credential file; recover the secrets backup")
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(role)))
        connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))
        store = dict(host=info.host, port=info.port, user=role, password=password, dbname=database,
                     sslmode=connection.info.get_parameters().get("sslmode", "prefer"))
        if connection.info.get_parameters().get("sslrootcert"):
            store["sslrootcert"] = connection.info.get_parameters()["sslrootcert"]
        # LightRAG maps 'prefer' to SSL=True; local Docker PostgreSQL has no TLS certificate.
        if cfg.app_env != "production" and store["sslmode"] == "prefer":
            store["sslmode"] = "disable"
        if cfg.app_env == "production" and store["sslmode"] != "verify-full":
            raise RuntimeError("Provision production tenants using a verify-full TLS admin DSN")
    admin = psycopg.conninfo.conninfo_to_dict(admin_dsn())
    admin["dbname"] = database
    with psycopg.connect(**admin, autocommit=True) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    stores[str(tenant)] = store
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as output:
        json.dump(stores, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def bootstrap():
    if settings().app_env == "production":
        raise RuntimeError("Demo bootstrap is forbidden in production; use tenant/member commands")
    engine = create_engine(settings().migration_database_url.get_secret_value())
    with Session(engine) as session, session.begin():
        for identifier, name in zip(DEMO_TENANTS, ["CNF Indonesia", "Tenant B"], strict=True):
            session.merge(Tenant(id=uuid.UUID(identifier), name=name, active=True))
        for name, identifier in DEMO_USERS.items():
            session.merge(User(id=identifier, name=name.title(), email=f"{name}@example.invalid", active=True))
        session.flush()
        for name in ["alice", "bob", "mallory"]:
            session.merge(Membership(tenant_id=uuid.UUID(DEMO_TENANTS[1 if name == "mallory" else 0]),
                                     user_id=DEMO_USERS[name], role="viewer" if name == "bob" else "tenant_admin",
                                     groups=["hr"] if name == "alice" else [], active=True, revision=1))
    for tenant in DEMO_TENANTS:
        provision_store(tenant)
    with psycopg.connect(admin_dsn(), autocommit=True) as connection:
        connection.execute("REVOKE CONNECT ON DATABASE enterprise FROM PUBLIC")
        connection.execute("GRANT CONNECT ON DATABASE enterprise TO rag_app,rag_worker")
    print("Demo tenants provisioned. Login password is in .secrets/dev_password (never commit it).")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap")
    revoke = commands.add_parser("revoke-sessions")
    revoke.add_argument("subject")
    replay = commands.add_parser("replay-deletions")
    replay.add_argument("archive", type=Path)
    replay.add_argument("--keys-file", type=Path)
    tenant = commands.add_parser("tenant")
    tenant.add_argument("id", type=uuid.UUID)
    tenant.add_argument("name")
    member = commands.add_parser("member")
    member.add_argument("tenant", type=uuid.UUID)
    member.add_argument("subject")
    member.add_argument("--name", default="Employee")
    member.add_argument("--role", choices=["viewer", "editor", "tenant_admin", "auditor"], default="viewer")
    member.add_argument("--groups", default="")
    member.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    if args.command in {"member", "revoke-sessions", "replay-deletions"} and len(settings().audit_hmac_key.get_secret_value()) < 32:
        raise RuntimeError("Offline administration requires AUDIT_HMAC_KEY_FILE for signed audit records")
    if args.command == "bootstrap":
        bootstrap()
        return
    if args.command == "replay-deletions":
        replay_deletions(args.archive, args.keys_file)
        return
    with Session(create_engine(settings().migration_database_url.get_secret_value())) as session, session.begin():
        if args.command == "revoke-sessions":
            session.execute(delete(LoginSession).where(LoginSession.user_id == args.subject))
            for membership in session.scalars(select(Membership).where(Membership.user_id == args.subject)):
                audit(session, Access(membership.tenant_id, "offline:" + getpass.getuser(), "system", (), 0), "identity.sessions_revoked", args.subject)
        elif args.command == "tenant":
            session.merge(Tenant(id=args.id, name=args.name, active=True))
        else:
            user = session.get(User, args.subject)
            if not user:
                session.add(User(id=args.subject, name=args.name, email="", active=True))
                session.flush()
            membership = session.get(Membership, (args.tenant, args.subject))
            if not membership:
                membership = Membership(tenant_id=args.tenant, user_id=args.subject)
                session.add(membership)
            membership.role, membership.groups, membership.active = args.role, [g for g in args.groups.split(",") if g], not args.disable
            membership.revision = (membership.revision or 0) + 1
            audit(session, Access(args.tenant, "offline:" + getpass.getuser(), "system", (), 0), "identity.membership_changed", args.subject,
                  {"active": membership.active, "role": membership.role, "groups": membership.groups, "revision": membership.revision})
    if args.command == "tenant":
        provision_store(args.id)


def replay_deletions(archive, keys_file=None):
    keys = json.loads(keys_file.read_text()) if keys_file else [settings().audit_hmac_key.get_secret_value()]
    if not keys or any(len(key) < 32 for key in keys):
        raise ValueError("Valid audit verification keys are required")
    replayed = 0
    with Session(create_engine(settings().migration_database_url.get_secret_value())) as session, session.begin():
        for path in sorted(archive.rglob("*.json")):
            event = json.loads(path.read_text())
            signature = event.pop("signature", "")
            body = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
            if not any(hmac.compare_digest(signature, hmac.new(key.encode(), body, hashlib.sha256).hexdigest()) for key in keys):
                raise ValueError("Audit archive signature mismatch; recovery cannot open traffic")
            if event["action"] not in {"document.delete_requested", "document.retention_expired", "document.purged"}:
                continue
            document = session.get(Document, uuid.UUID(event["resource"]))
            if not document or str(document.tenant_id) != event["tenant_id"] or document.deleted_at:
                continue
            if document.legal_hold:
                raise ValueError("Restored legal hold conflicts with deletion ledger; resolve with data owner")
            document.deleted_at, document.status = now(), "deleting"
            document.acl_revision += 1
            session.add(Job(tenant_id=document.tenant_id, document_id=document.id, kind="purge"))
            audit(session, Access(document.tenant_id, "offline:" + getpass.getuser(), "system", (), 0), "recovery.deletion_replayed", str(document.id))
            replayed += 1
    print(f"Verified archive and replayed {replayed} deletion tombstones")


if __name__ == "__main__":
    main()
