# ADR-0007 — SQLite for development, PostgreSQL in CI

- **Status** Accepted
- **Date** 2026-08-28

## Context

A repository meant to be cloned and run must start with `make install && make test` on a bare
machine, with no database to install. But an application whose persistence adapter is only ever
exercised against SQLite has an untested adapter: SQLite is permissive about types, lenient
about constraints, and silently different on transaction and concurrency semantics.

## Decision

Two targets for the same adapter:

- **Development and the default test run** use SQLite via `aiosqlite`, requiring nothing installed.
- **CI additionally runs the integration and persistence suites against real PostgreSQL** in a
  service container, via `asyncpg`, driven by `ACADEMY_DATABASE_URL`.

The same Alembic migrations and the same repository code run against both. A test that cannot
run on a backend skips with a stated reason rather than being silently dropped.

## Consequences

- Cloning and running stays trivial, and the adapter is still verified against the database it
  targets in production.
- Dialect-specific problems — reserved words, timezone-aware timestamps, `ON CONFLICT` syntax —
  surface in CI rather than in production.
- CI is slower and has one more moving part.
- Two backends must stay supported, which occasionally constrains the SQL the adapter may use.
  That constraint is itself instructive: it keeps the adapter honest about being an adapter.

## Alternatives considered

- **SQLite only.** Simplest, and leaves the production database untested.
- **PostgreSQL only, via Docker, everywhere.** Highest fidelity, and it makes Docker a
  prerequisite for running the test suite at all — too high a barrier for a teaching repository.
- **testcontainers locally.** A good middle ground, and still requires a working Docker daemon
  on the reader's machine.
