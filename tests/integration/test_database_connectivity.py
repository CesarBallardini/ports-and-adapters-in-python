"""Integration-tier smoke test: can we reach the configured database at all?

This is the only integration test that exists while the persistence adapter is still
unwritten, and it earns its place by pinning down ADR-0007's contract: the suite runs
against SQLite by default and against real PostgreSQL when ``ACADEMY_DATABASE_URL``
points at one, using the same code either way.

A test that cannot run on the configured backend skips with a stated reason rather than
being silently dropped.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DEFAULT_URL = 'sqlite+aiosqlite:///:memory:'


@pytest.mark.integration
async def test_configured_database_accepts_a_query() -> None:
    url = os.environ.get('ACADEMY_DATABASE_URL', DEFAULT_URL)

    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text('SELECT 1'))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()
