"""The engine, the sessions, and getting a database to the schema it needs.

Three small things that every deployment and every test needs in the same order: migrate, open
an engine, hand out sessions.

Migrating is **not** optional and **not** conditional. There is no ``create_all`` anywhere in
this repository (ADR-0006), so a database that has not been migrated has no tables at all --
which is the state a test starts from and the state a fresh deployment starts from, and the
reason the schema under test is the schema that will be deployed.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from academy.adapters.outbound.persistence.sqlalchemy.mappers import configure_mappers_once
from alembic import command


def _project_root() -> Path:
    """Find the directory holding ``alembic.ini``, by walking up from this file.

    Searched rather than counted. ``parents[6]`` is correct today and is wrong the moment a
    module moves one level, and it is wrong *silently* -- the failure is Alembic reporting that
    a path does not exist, several layers from the cause. Walking up terminates at the file it
    is looking for or not at all.

    Raises:
        FileNotFoundError: If there is no ``alembic.ini`` above this module. That means the
            package was installed without the migrations beside it, and no database it connects
            to can be brought to a schema -- which is worth saying at startup rather than at the
            first query.
    """
    for directory in Path(__file__).resolve().parents:
        if (directory / 'alembic.ini').is_file():
            return directory
    raise FileNotFoundError('no alembic.ini above this module: the migrations are not installed')


# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
PROJECT_ROOT = _project_root()
ALEMBIC_INI = PROJECT_ROOT / 'alembic.ini'


def migrate_to_head(url: str) -> None:
    """Bring the database at ``url`` up to the newest migration.

    Synchronous, because Alembic is: ``env.py`` opens its own async engine and drives it with
    ``asyncio.run``. That means this must **not** be called from inside a running event loop --
    a test fixture calls it before the loop starts, and a process calls it at startup.

    Args:
        url: An async SQLAlchemy URL, the same one the application will connect with. Passed
            through the environment because that is what ``env.py`` reads, so "migrate it" and
            "run against it" cannot end up pointing at two different databases.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option('script_location', str(PROJECT_ROOT / 'alembic'))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, 'head')


def downgrade_to_base(url: str) -> None:
    """Undo every migration on the database at ``url``.

    Exists to be *tested*. A downgrade nobody runs is a downgrade nobody knows is broken, and
    the day it matters is not the day to find out.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option('script_location', str(PROJECT_ROOT / 'alembic'))
    config.set_main_option('sqlalchemy.url', url)
    command.downgrade(config, 'base')


def create_engine(url: str) -> AsyncEngine:
    """Open the engine a process will use for its lifetime.

    Also the moment the domain classes are bound to the tables: mapping is a fact about the
    process, and doing it here means no repository can be constructed against an unmapped
    class.
    """
    configure_mappers_once()
    return create_async_engine(url)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the factory a scope opens one session from.

    ``expire_on_commit=False`` deliberately. With it on, every attribute of every object a use
    case is still holding becomes a lazy load the moment the unit of work commits -- and a lazy
    load in an async session raises, at a point in the code that has nothing to do with the
    cause. Use cases here finish with domain objects in hand and expect them to keep working.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
