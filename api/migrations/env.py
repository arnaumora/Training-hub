"""Alembic environment. Migrations are plain SQL-level operations; there is no ORM model metadata."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url() -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "db"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not used in this project")
run_migrations_online()
