from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# IMPORTANT: ensures all models are imported and registered with Base.metadata
# Without this, Alembic autogenerate won't detect your tables
import packages.db.models  # noqa: F401
from alembic import context
from packages.config import settings
from packages.db.base import Base

# Alembic Config object (reads alembic.ini)
config = context.config

# --- DATABASE URL SETUP ---

# Alembic runs synchronously, but app likely uses asyncpg
# Replace async driver with sync psycopg driver
sync_url = settings.database_url.replace("+asyncpg", "+psycopg")

# Inject runtime DB URL into Alembic config
config.set_main_option("sqlalchemy.url", sync_url)

# --- LOGGING SETUP ---

# Configure logging from alembic.ini if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used for autogeneration (models → DB schema comparison)
target_metadata = Base.metadata


# --- OFFLINE MIGRATIONS ---

def run_migrations_offline() -> None:
    """
    Run migrations without a live database connection.

    Generates SQL statements instead of executing them.
    Useful for:
    - CI pipelines
    - Reviewing SQL before applying
    """

    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,

        # Render bound values directly into SQL
        literal_binds=True,

        # Use named parameters (better readability in generated SQL)
        dialect_opts={"paramstyle": "named"},
    )

    # Begin transaction context and run migrations
    with context.begin_transaction():
        context.run_migrations()


# --- ONLINE MIGRATIONS ---

def run_migrations_online() -> None:
    """
    Run migrations with a live database connection.

    This is the normal mode when applying migrations.
    """

    # Create SQLAlchemy engine from config
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",

        # Disable connection pooling (recommended for migrations)
        poolclass=pool.NullPool,
    )

    # Establish DB connection
    with connectable.connect() as connection:

        # Bind Alembic to this connection and metadata
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        # Run migrations inside a transaction
        with context.begin_transaction():
            context.run_migrations()


# --- ENTRY POINT ---

# Decide whether to run offline or online mode
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
