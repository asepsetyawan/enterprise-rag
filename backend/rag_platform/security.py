import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import HTTPException, Request

from .config import settings
from .db import transaction
from .models import Audit, AuditDelivery, Document, LoginSession, Membership, Tenant, User, now


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def internal_auth(request: Request):
    supplied = request.headers.get("X-Internal-Token", "")
    if not hmac.compare_digest(supplied, settings().internal_token.get_secret_value()):
        raise HTTPException(401, "Invalid internal credential")


@dataclass(frozen=True)
class Identity:
    user_id: str
    csrf: str = ""
    bearer: bool = False


@dataclass(frozen=True)
class Access:
    tenant_id: uuid.UUID
    user_id: str
    role: str
    groups: tuple[str, ...]
    revision: int


_jwks = {"expires": 0, "data": {}}


def internal_oidc_url(url: str) -> str:
    cfg = settings()
    public = urlsplit(cfg.oidc_issuer)
    target = urlsplit(url)
    if (target.scheme, target.netloc) != (public.scheme, public.netloc):
        raise RuntimeError("OIDC endpoint is outside the configured issuer origin")
    return cfg.oidc_internal_base.rstrip("/") + target.path + ("?" + target.query if target.query else "")


async def bearer_identity(token: str) -> Identity:
    cfg = settings()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError("Unsupported token algorithm")
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            if _jwks["expires"] < time.monotonic() or not any(key.get("kid") == header.get("kid") for key in _jwks["data"].get("keys", [])):
                response = await client.get(internal_oidc_url(cfg.oidc_issuer + "/protocol/openid-connect/certs"))
                response.raise_for_status()
                _jwks.update(expires=time.monotonic() + 300, data=response.json())
        key_data = next(key for key in _jwks["data"]["keys"] if key.get("kid") == header.get("kid"))
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key_data).key, algorithms=["RS256"],
                            audience=cfg.oidc_client_id, issuer=cfg.oidc_issuer,
                            options={"require": ["exp", "iat", "sub", "iss", "aud"]}, leeway=10)
        service = claims.get("azp") in cfg.oidc_service_clients.split(",")
        if cfg.oidc_required_acr and not service and str(claims.get("acr", "")) != cfg.oidc_required_acr:
            raise ValueError("Insufficient authentication assurance")
        return Identity(claims["sub"], bearer=True)
    except (ValueError, KeyError, StopIteration, jwt.PyJWTError, httpx.HTTPError) as exc:
        raise HTTPException(401, "Invalid access token") from exc


async def identity(request: Request) -> Identity:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return await bearer_identity(authorization[7:])
    token = request.cookies.get(settings().cookie_name, "")
    if not token:
        raise HTTPException(401, "Login required")
    token_hash = digest(token)
    with transaction(session_hash=token_hash) as session:
        row = session.get(LoginSession, token_hash)
        if not row or row.expires_at <= now():
            raise HTTPException(401, "Session expired")
        result = Identity(row.user_id, row.csrf)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != settings().public_origin.rstrip("/"):
            raise HTTPException(403, "Invalid request origin")
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), result.csrf):
            raise HTTPException(403, "CSRF token required")
    return result


def access_for(identity_: Identity, tenant_id) -> Access:
    try:
        tenant_id = uuid.UUID(str(tenant_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "A valid X-Tenant-ID header is required") from exc
    with transaction(tenant_id, identity_.user_id) as session:
        membership = session.get(Membership, (tenant_id, identity_.user_id))
        user = session.get(User, identity_.user_id)
        tenant = session.get(Tenant, tenant_id)
        if not tenant or not tenant.active or not user or not user.active or not membership or not membership.active:
            raise HTTPException(403, "Tenant access denied")
        return Access(tenant_id, identity_.user_id, membership.role, tuple(membership.groups), membership.revision)


def can_read(document: Document, access: Access) -> bool:
    if document.tenant_id != access.tenant_id or document.deleted_at:
        return False
    if document.retention_until and document.retention_until <= now() and not document.legal_hold:
        return False
    return (document.owner_id == access.user_id or access.user_id in document.acl_users
            or "all" in document.acl_groups or bool(set(document.acl_groups) & set(access.groups)))


def can_manage(document: Document, access: Access) -> bool:
    return document.tenant_id == access.tenant_id and (document.owner_id == access.user_id or access.role == "tenant_admin")


def require_read(session, document_id, access):
    document = session.get(Document, document_id)
    if not document or not can_read(document, access):
        raise HTTPException(404, "Document not found")
    return document


def audit(session, access: Access, action: str, resource: str, detail=None, request_id=""):
    identifier = uuid.uuid4()
    created_at = now()
    body = {"id": str(identifier), "tenant_id": str(access.tenant_id), "actor_id": access.user_id,
            "action": action, "resource": str(resource), "detail": detail or {},
            "request_id": request_id, "created_at": created_at.isoformat()}
    signature = hmac.new(settings().audit_hmac_key.get_secret_value().encode(),
                         json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
    session.add(Audit(id=identifier, tenant_id=access.tenant_id, actor_id=access.user_id, action=action,
                      resource=str(resource), detail=detail or {}, request_id=request_id,
                      created_at=created_at, signature=signature))
    session.flush()
    session.add(AuditDelivery(id=identifier, tenant_id=access.tenant_id))


def new_session(user_id, name, email=""):
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    hashed = digest(token)
    with transaction(user=user_id, session_hash=hashed) as session:
        user = session.get(User, user_id)
        if not user:
            session.add(User(id=user_id, name=name[:200], email=email[:320]))
            session.flush()
        elif not user.active:
            raise HTTPException(403, "Account disabled")
        session.add(LoginSession(token_hash=hashed, user_id=user_id, csrf=csrf,
                                 expires_at=now() + timedelta(hours=settings().session_hours)))
    return token
