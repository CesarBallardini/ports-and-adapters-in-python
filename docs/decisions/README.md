# Architecture decision records

One file per decision, in the format set by [ADR-0001](./0001-record-architecture-decisions.md).
Accepted ADRs are immutable: a decision that stops holding is superseded by a new one that
references it, never edited in place.

| # | Decision | Status |
|---|----------|--------|
| [0001](./0001-record-architecture-decisions.md) | Record architecture decisions in ADRs | Accepted |
| [0002](./0002-reuse-the-academy-domain.md) | Reuse the academy domain unchanged | Accepted |
| [0003](./0003-ports-and-adapters-layering.md) | Ports and adapters with four layers | Accepted |
| [0004](./0004-enforce-the-dependency-rule.md) | Enforce the dependency rule mechanically | Accepted |
| [0005](./0005-async-io-ports-sync-cpu-ports.md) | Async ports for I/O, sync ports for CPU | Accepted |
| [0006](./0006-sqlalchemy-imperative-mapping.md) | SQLAlchemy imperative mapping, Alembic for schema | Accepted |
| [0007](./0007-postgresql-in-ci-sqlite-in-development.md) | SQLite for development, PostgreSQL in CI | Accepted |
| [0008](./0008-one-spreadsheet-port-two-adapters.md) | One spreadsheet port, CSV and XLSX adapters | Accepted |
| [0009](./0009-imports-inline-or-queued-by-size.md) | Imports run inline or queued, chosen by file size | Accepted |
| [0010](./0010-session-cookie-web-bearer-api.md) | Session cookie for the web, Bearer token for the API | Accepted |
| [0011](./0011-htmx-for-the-web-adapter.md) | htmx and Jinja2 for the web adapter | Accepted |
| [0012](./0012-domain-error-to-http-status-table.md) | One declarative DomainError to HTTP status table | Accepted |
| [0013](./0013-testing-strategy.md) | Testing strategy and tiers | Accepted |
| [0014](./0014-in-memory-adapters-and-contract-tests.md) | In-memory adapters are first-class, verified by contract tests | Accepted |
| [0015](./0015-manual-composition-root-over-a-di-container.md) | Manual composition root over a DI container | Accepted |
