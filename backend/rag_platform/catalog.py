"""Lexical document routing. Authorization is part of SQL before ranking/limit."""
import re

from sqlalchemy import and_, func, or_, select

from .config import settings
from .models import Document, DocumentVersion, now

STOP_WORDS = set("apa apakah bagaimana berapa yang dan di ke dari untuk dengan pada dalam itu ini saya kami kita boleh bisa adalah kapan siapa mengapa the a an is are what how much many of in on to for and please".split())


def query_terms(question):
    # Only alphanumeric tokens enter tsquery syntax; no user-supplied operators.
    return list(dict.fromkeys(word for word in re.findall(r"[^\W_]+", question.casefold())
                              if len(word) > 1 and word not in STOP_WORDS))[:32]


def readable_documents(actor):
    return and_(
        Document.tenant_id == actor.tenant_id, Document.deleted_at.is_(None),
        or_(Document.retention_until.is_(None), Document.retention_until > now(), Document.legal_hold),
        or_(Document.owner_id == actor.user_id, Document.acl_users.contains([actor.user_id]),
            Document.acl_groups.contains(["all"]),
            *(Document.acl_groups.contains([group]) for group in actor.groups)),
    )


def select_documents(session, question, actor, document_ids):
    query = select(Document, DocumentVersion).join(
        DocumentVersion, and_(DocumentVersion.id == Document.active_version_id,
                              DocumentVersion.document_id == Document.id,
                              DocumentVersion.tenant_id == Document.tenant_id),
    ).where(readable_documents(actor), Document.status == "ready",
            DocumentVersion.status == "ready", DocumentVersion.workspace.is_not(None))
    if document_ids:
        return session.execute(query.where(Document.id.in_(document_ids)).order_by(Document.id)).all()
    terms = query_terms(question)
    if not terms:
        return []
    tsquery = func.to_tsquery("simple", " | ".join(terms))
    return session.execute(query.where(DocumentVersion.search_vector.op("@@")(tsquery))
                           .order_by(func.ts_rank_cd(DocumentVersion.search_vector, tsquery).desc(), Document.id)
                           .limit(settings().routing_top_k)).all()
