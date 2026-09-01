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
| [0016](./0016-who-may-read-a-section-grade-sheet.md) | A grade sheet is readable only by someone who may read every person on it | Accepted |
| [0017](./0017-value-object-collections-as-serialised-columns.md) | Value-object collections are stored as serialised columns | Accepted |
| [0018](./0018-two-database-roles.md) | Two database roles: migrations own the schema, the application owns the data | Accepted |
| [0019](./0019-one-failure-classification-rendered-per-adapter.md) | One failure classification, rendered per inbound adapter | Accepted |
| [0020](./0020-argparse-cli-with-an-asserted-actor.md) | argparse for the CLI, with the actor named by `--as` | Accepted |
| [0021](./0021-one-driving-port-per-route.md) | One driving port per route, and one error boundary | Accepted |
| [0022](./0022-two-actor-identity-adapters-and-a-placeholder-credential-check.md) | Two `ActorIdentity` adapters, and a labelled placeholder credential check | Accepted |
