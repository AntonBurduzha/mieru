import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from mieru.core.config import get_settings
from mieru.models import Base

config = context.config

# The tests set configure_logger to False, so Alembic leaves pytest's logging alone
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
  fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
  """sqlalchemy.url when the caller set it, else DATABASE_URL from the settings."""
  url = config.get_main_option("sqlalchemy.url")
  if url:
    return url
  return str(get_settings().database_url.get_secret_value())


def run_migrations_offline() -> None:
  """Print the SQL instead of running it: `alembic upgrade head --sql`."""
  context.configure(
    url=database_url(),
    target_metadata=target_metadata,
    literal_binds=True,
    dialect_opts={"paramstyle": "named"},
  )
  with context.begin_transaction():
    context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
  """Run the scripts on one connection, inside one transaction."""
  context.configure(connection=connection, target_metadata=target_metadata)
  with context.begin_transaction():
    context.run_migrations()


async def run_migrations_online() -> None:
  """Connect with asyncpg and hand the connection to Alembic's synchronous code."""
  # NullPool: a one-off command keeps no connections open after it ends.
  # lock_timeout: a migration that waits over 10 s for a lock (an ALTER behind an open
  # transaction) fails, so the deploy stops instead of queueing every query behind it
  engine = create_async_engine(
    database_url(),
    poolclass=pool.NullPool,
    connect_args={"server_settings": {"lock_timeout": "10s"}},
  )
  async with engine.connect() as connection:
    await connection.run_sync(do_run_migrations)
  await engine.dispose()


if context.is_offline_mode():
  run_migrations_offline()
else:
  asyncio.run(run_migrations_online())
