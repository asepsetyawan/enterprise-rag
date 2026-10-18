from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from .config import settings


@lru_cache
def engine(worker=False):
    cfg = settings()
    url = cfg.worker_database_url if worker and cfg.worker_database_url else cfg.database_url
    return create_engine(url.get_secret_value(), pool_pre_ping=True, pool_size=8, max_overflow=8)


def context(session: Session, tenant="", user="", session_hash=""):
    session.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.user_id', :u, true), set_config('app.session_hash', :s, true)"),
                    {"t": str(tenant or ""), "u": str(user or ""), "s": session_hash})


@contextmanager
def transaction(tenant="", user="", session_hash="", worker=False):
    with Session(engine(worker)) as session:
        with session.begin():
            context(session, tenant, user, session_hash)
            yield session


def verify_runtime_role(worker=False):
    with engine(worker).connect() as conn:
        role = conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
        if role.rolsuper or role.rolbypassrls:
            raise RuntimeError("API must connect using a non-superuser without BYPASSRLS")
