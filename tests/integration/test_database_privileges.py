"""The application role must not be able to change the schema (ADR-0018).

`integration` tier, and **PostgreSQL only** — deliberately, because that is the only place the
guarantee exists. SQLite has no permission system, so on a developer's machine the two URLs name
one file and the separation is documentation; here it is a checked property.

This is the test the ADR is worth having. Two connection strings that happen to differ prove
nothing at all: without an assertion that the application role is actually refused, "least
privilege" is a paragraph in a document and a role somebody may have granted `ALL` to in a
hurry.

The roles are created by the test rather than assumed, so the assertion holds on any PostgreSQL
the suite is pointed at and does not depend on `scripts/create-database-roles.sql` having been
run first. What it *does* share with that script is the shape: a schema owned by the migrator,
`USAGE` and DML for the application, and no `CREATE` anywhere.
"""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

# The application role this test creates and drops. Named for the test so that a failure part
# way through cannot collide with a real deployment's role on a shared cluster.
APP_ROLE = 'academy_app_privilege_test'
APP_PASSWORD = 'privilege-test'  # noqa: S105 - a throwaway role on a throwaway database
SCHEMA = 'academy_privilege_test'


def _database_url() -> str:
    """The database under test, or skip."""
    url = os.environ.get('ACADEMY_DATABASE_URL', '')
    if not url.startswith('postgresql'):
        pytest.skip('privilege separation is a PostgreSQL guarantee; SQLite has no roles (ADR-0018)')
    return url


def _as_role(url: str, role: str, password: str) -> str:
    """Rewrite a URL to connect as another role.

    Crude on purpose: the test owns both halves of this string and a URL parser would be more
    code saying the same thing.
    """
    scheme, _, rest = url.partition('://')
    _, _, host_and_db = rest.rpartition('@')
    return f'{scheme}://{role}:{password}@{host_and_db}'


@pytest.fixture
async def application_role() -> AsyncIterator[str]:
    """Create a schema and an application role with DML but no DDL, and clean both up."""
    url = _database_url()
    admin = create_async_engine(url, isolation_level='AUTOCOMMIT')
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE'))
            await connection.execute(text(f'DROP ROLE IF EXISTS {APP_ROLE}'))
            await connection.execute(text(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"))
            await connection.execute(text(f'CREATE SCHEMA {SCHEMA}'))
            # Exactly what the role script grants: enough to use the schema, nothing to change
            # it. No CREATE, and no ownership of anything.
            await connection.execute(text(f'GRANT USAGE ON SCHEMA {SCHEMA} TO {APP_ROLE}'))
            await connection.execute(text(f'ALTER ROLE {APP_ROLE} SET search_path = {SCHEMA}'))

            # A table the migrator owns, as a migration would have left it, and DML on it for
            # the application. Without a real table the DROP assertion below is vacuous:
            # `DROP TABLE IF EXISTS` on nothing succeeds without checking a single privilege.
            await connection.execute(text(f'CREATE TABLE {SCHEMA}.people (id text primary key)'))
            await connection.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {SCHEMA}.people TO {APP_ROLE}'))

        yield _as_role(url, APP_ROLE, APP_PASSWORD)

        async with admin.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE'))
            await connection.execute(text(f'DROP ROLE IF EXISTS {APP_ROLE}'))
    finally:
        await admin.dispose()


@pytest.mark.integration
async def test_the_application_role_cannot_create_a_table(application_role: str) -> None:
    # The whole point of two roles. An injected DDL statement, a dependency that decides to
    # create a table, or application code that reaches for `op.` all end here.
    engine = create_async_engine(application_role)
    try:
        async with engine.connect() as connection:
            with pytest.raises(ProgrammingError, match='permission denied'):
                await connection.execute(text('CREATE TABLE smuggled (id text)'))
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_the_application_role_cannot_drop_a_table_it_uses(application_role: str) -> None:
    # A real table, owned by the migrator. Dropping is the privilege that turns a bug or an
    # injection into data loss rather than an error.
    engine = create_async_engine(application_role)
    try:
        async with engine.connect() as connection:
            with pytest.raises(ProgrammingError, match='must be owner'):
                await connection.execute(text('DROP TABLE people'))
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_the_application_role_cannot_alter_a_table_it_uses(application_role: str) -> None:
    engine = create_async_engine(application_role)
    try:
        async with engine.connect() as connection:
            with pytest.raises(ProgrammingError, match='must be owner'):
                await connection.execute(text('ALTER TABLE people ADD COLUMN smuggled text'))
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_the_application_role_can_still_read_and_write_rows(application_role: str) -> None:
    # The other half, and the one a too-tight grant breaks: a role that cannot change the
    # schema must still be able to work inside it, or the separation has cost an application.
    engine = create_async_engine(application_role)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO people (id) VALUES ('p1')"))
            assert (await connection.execute(text('SELECT count(*) FROM people'))).scalar_one() == 1
            await connection.execute(text('DELETE FROM people'))
    finally:
        await engine.dispose()
