from alembic import context
from sqlalchemy import create_engine

from rag_platform.config import settings
from rag_platform.models import Base

cfg = settings()
url = cfg.migration_database_url
if not url:
    raise RuntimeError("MIGRATION_DATABASE_URL is required only by the migration process")
with create_engine(url.get_secret_value()).connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()
