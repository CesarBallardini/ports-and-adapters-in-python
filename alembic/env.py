"""Alembic's entry point: how a migration reaches a database.

Async throughout, because the application's URLs are async ones -- ``sqlite+aiosqlite`` and
``postgresql+asyncpg`` (ADR-0007). Converting them to synchronous drivers here would mean
migrations run against a connection the application never uses, and would need two more drivers
installed for the privilege.

The URL comes from ``ACADEMY_DATABASE_URL``, the same variable the integration suite reads, so
"migrate the database" and "run against the database" cannot disagree about which one.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from academy.adapters.outbound.persistence.sqlalchemy.tables import metadata
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# What `--autogenerate` compares against, and what a reviewer checks a hand-written migration
# for. The tables are declared in one place (`tables.py`) and the migrations are the record of
# how a database gets there; neither is generated from the other without a human reading it.
target_metadata = metadata

DEFAULT_URL = 'sqlite+aiosqlite:///./academy_development.db'


def _url() -> str:
    """The database to migrate, and the role to migrate it as.

    ``ACADEMY_MIGRATION_DATABASE_URL`` first, because migrations are the only thing in this
    system that issues DDL and they should be the only thing holding a role that can (ADR-0018).
    It falls back to ``ACADEMY_DATABASE_URL`` so that a developer on SQLite -- where there are
    no roles to separate -- configures one URL and gets a working database.

    A PostgreSQL deployment that leaves the migration URL unset still works, and has simply
    given up the separation. That is a deployment decision, so it is not refused here; the
    integration suite is where the separation is asserted, against a database that has roles.
    """
    # The config comes first, and only a *programmatic* caller can set it: `migrate_to_head`
    # passes the URL it was given. Without this, a test that migrates a temporary database
    # silently migrates the default one instead and then fails several layers away, reporting
    # a missing table rather than a missing database.
    configured = config.get_main_option('sqlalchemy.url', '')
    if configured:
        return configured

    return os.environ.get('ACADEMY_MIGRATION_DATABASE_URL') or os.environ.get('ACADEMY_DATABASE_URL') or DEFAULT_URL


def run_migrations_offline() -> None:
    """Emit SQL for a database this process will not connect to.

    What a DBA runs when the application has no credentials for production, and what makes a
    migration reviewable as a diff of statements rather than as Python.
    """
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        # Named constraints, so a downgrade can drop by name on both databases (see
        # `tables.NAMING_CONVENTION`), and batch mode, so SQLite can alter a table at all.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    """Run every pending migration on an open connection."""
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect, migrate, and dispose.

    Disposing matters here in a way it usually does not: the test suite migrates a fresh
    database per test, and an engine left holding a SQLite file keeps a handle that Windows
    will not let the temporary directory delete.
    """
    section = config.get_section(config.config_ini_section, {})
    section['sqlalchemy.url'] = _url()
    engine = async_engine_from_config(section, prefix='sqlalchemy.')

    async with engine.connect() as connection:
        await connection.run_sync(_run)

    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
