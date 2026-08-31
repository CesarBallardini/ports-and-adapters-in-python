# ADR-0018 — Two database roles: migrations own the schema, the application owns the data

- **Status** Accepted
- **Date** 2026-08-31

## Context

ADR-0006 established that the schema comes from Alembic migrations and never from
`metadata.create_all()`. That settles *how* the schema is built. It leaves open *who* may build
it, and the default answer — whoever the application connects as — gives every request in the
system the power to drop a table.

Nothing in `academy` issues DDL. Repositories select, insert, update and delete; the unit of
work commits. A connection able to do more than that carries privileges only a mistake, a bug or
an injection could ever use.

## Decision

Two connection strings, and behind them two database roles.

| Variable | Used by | May |
|---|---|---|
| `ACADEMY_DATABASE_URL` | the application: engine, repositories, unit of work | `SELECT`, `INSERT`, `UPDATE`, `DELETE` |
| `ACADEMY_MIGRATION_DATABASE_URL` | Alembic's `env.py`, and nothing else | own the schema: `CREATE`, `ALTER`, `DROP` |

The migration URL falls back to the application URL when unset, so a developer configures one
variable and gets a working database. `scripts/create-database-roles.sql` creates the two roles
and is run once, by a superuser, before the first migration — it cannot be a migration itself,
because a migration runs *as* the migrator.

Two naming decisions come with it.

**The tables live in a schema called `academy`, not in `public`.** PostgreSQL historically grants
`CREATE` on `public` to every role, so an application role that "cannot do DDL" can still create
tables there until somebody remembers to revoke it. A schema owned by `academy_migrator`, with
only `USAGE` granted to `academy_app`, makes the separation structural instead of a revoke you
must not forget.

The schema is selected **per role** with `search_path`, never written into the table names:

```sql
ALTER ROLE academy_app IN DATABASE :"database" SET search_path = academy;
```

Qualifying the metadata as `academy.people` would emit DDL that SQLite cannot run, and the same
migrations must work on both databases (ADR-0007). This way `tables.py` and every migration stay
unqualified and identical, and only the connection differs.

**Databases are named for their environment** — `academy_production`, `academy_staging`,
`academy_test`. The name in a connection string then says which environment it belongs to, so a
restore or a migration aimed at the wrong one is a visible mistake rather than a silent success.
The cost is that connection strings are not interchangeable between environments, which is the
same property read the other way round.

Two consequences follow, and both are the point rather than side effects:

- **The application never migrates.** `create_app()` does not call `migrate_to_head`, because
  with two roles it could not. Migration becomes a deliberate deploy step — a command run before
  the new code starts — which is where it belonged anyway.
- **The default-privileges grant is mandatory.** Granting on *existing* tables is not enough:
  without `ALTER DEFAULT PRIVILEGES FOR ROLE academy_migrator … GRANT … TO academy_app`, the
  next migration creates a table the application cannot read, and the failure surfaces at run
  time in production rather than at deploy time. Every future migration would be a latent
  outage. That single statement is the most important line in the script.

## Consequences

- An injected `DROP TABLE`, a mistaken `op.` call from application code, or a dependency that
  decides to "helpfully" create a table all fail with a permission error instead of succeeding.
- **SQLite cannot enforce any of it.** It has no permission system; access is file permissions
  and nothing else. On a developer's machine the two URLs name one file and the separation is
  documentation. It is real exactly where CI and production run PostgreSQL (ADR-0007) — which
  is worth saying out loud rather than implying that the local setup proves anything.
- Because of that, the separation is **asserted by a test on the PostgreSQL leg only**: the
  application role attempts `CREATE TABLE` and must be refused. Two URLs that happen to hold the
  same string prove nothing, and that test is the difference between a convention and a checked
  property.
- Operationally there are now two secrets rather than one, and a deployment that forgets the
  migration URL silently runs migrations as the application role. It works, and it gives up the
  separation — so the fallback is a convenience with a cost, recorded here rather than hidden.
- Environment-named databases mean a connection string cannot be copied from one environment to
  another and still work, which is the point. It also means the name is one more thing that must
  match between the role script, the deployment and CI; the script takes it as a parameter
  rather than hard-coding it, so there is one place to get it wrong instead of four.

## Alternatives considered

- **One role with full privileges.** What most projects do, and simplest to operate. Rejected
  because it gives the request-serving path a capability it never uses, and the whole argument
  of this repository is that a component should be handed only what its job requires.
- **Migrate at application startup.** Convenient — one process, no deploy step — and it forces
  the application role to own the schema, which is exactly the privilege this ADR removes. It
  also means two instances starting together race on the same migration.
- **A read-only application role.** Too far: the application legitimately writes rows. The line
  is DDL versus DML, not read versus write.
