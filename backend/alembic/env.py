from sqlalchemy import create_engine, pool

from alembic import context
from drifttrace.config import get_settings
from drifttrace.db.models import Base

target_metadata = Base.metadata


def run() -> None:
    engine = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run()
