# ADR-0020 — argparse for the CLI, with the actor named by `--as`

- **Status** Accepted
- **Date** 2026-08-31
- **Amends** [ADR-0010](./0010-session-cookie-web-bearer-api.md), which assigned
  `Authorization: Bearer <token>` to the API **and** the CLI. The API half stands. The CLI half
  is replaced here, for the reasons in the first section below.

## Context

The CLI is the first inbound adapter this repository builds, chosen deliberately: it is the
smallest one, so the first driver written teaches the shape of a driving adapter instead of
fighting a framework. It is also the fourth driver in claim 3 of the README — *a use case should
not know who called it* — and the first that can actually demonstrate it, because it reaches the
same `ImportService` as the acceptance suite does, with no HTTP anywhere in the process.

Two things had to be decided before a line of it could be written.

### Why not a Bearer token

ADR-0010 gave the CLI `Authorization: Bearer <token>`, and that was wrong in a way that only
became visible once there was something to write. `Authorization` is an HTTP header. A CLI that
carried one would be pretending to make a request it never makes: there is no server in the
process, no header to put it in, and no token issuer anywhere in this system — ADR-0010 itself
says credential verification is "a teaching placeholder".

More decisive is the threat model. The CLI's credential is `ACADEMY_DATABASE_URL`. Anyone who
can run `python -m academy` already holds the application role's password and can read and write
every row with `psql` and no academy code at all. A token checked *after* that point guards a
door in a wall that is not there, and the security it appears to add is the dangerous kind — the
kind someone later relies on.

### The actor still has to be real

None of that makes the actor optional. Every command carries one (ADR-0010), `AccessGuard`
refuses what the relations do not grant, and an `Actor` rebuilt from an id alone has no roles and
is therefore a *different* actor, not a smaller one.

## Decision

**One command-line option names the actor, and the CLI trusts it.**

`--as <email>` is required by every command that reaches a use case. The CLI looks the address up
through `PersonRepository.by_email` and builds `Actor(person.id, roles=person.roles)` from the
person's **current** roles, read on this invocation. An address that names nobody is an error —
exit 4, `NOT_FOUND` — never an anonymous run and never an actor with an empty role set.

So identity is **asserted, not authenticated**, and authorization is enforced exactly as it is
for every other driver: `--as` decides *who you claim to be*, and `AccessPolicy` still decides
what that person may do. `academy grades record --as a-student@example.edu …` is refused, by the
same code that refuses it over HTTP.

**argparse**, from the standard library, parses it. The hexagon core deliberately has no
dependencies and every adapter family is an optional extra (see `pyproject.toml`); the CLI is the
one adapter that can be written with no extra at all, and a subcommand tree this size is what
argparse is for.

The CLI does **not** use the `ActorIdentity` port. That port resolves an *already authenticated*
person id, and the CLI has no authentication step to produce one — its precondition never holds
here. The port gets its adapter when the web adapter lands with a real session, which is also
when there will be two callers to keep honest.

## Consequences

- **The CLI is an administrative tool, and its access boundary is the database URL.** Anyone who
  may run it may impersonate anyone. That is a smaller grant than it sounds — they already have
  the database — but it must never be exposed as a remote shell to people who should not have
  full data access, and it is why there is no `--as` default: an operator names the person they
  are acting for, every time, in the shell history and the audit trail.
- **Roles are never stale.** They are read from the person record on each invocation, so a
  teacher removed from the staff at 10:00 cannot import at 10:01 because a script remembered
  what they used to be.
- No new dependency, and `python -m academy` works from a bare `uv sync` with no extras.
- argparse's own failures exit 2, which is why the classified failures of
  [ADR-0019](./0019-one-failure-classification-rendered-per-adapter.md) start at 3.
- A subcommand tree written by hand is more verbose than Typer's decorators, and every new
  command costs a parser stanza as well as a handler. That cost is real and it is paid in
  `adapters/inbound/cli/parser.py`.
- ADR-0010 now has a stale sentence in it. It is not edited — accepted ADRs are not rewritten —
  which is why this one leads with what it amends.

## Alternatives considered

- **Typer.** The nicest of the three to write: type hints become the parser, and the help output
  is good by default. It pulls in Click and Rich, which would make the smallest adapter in the
  repository the one that adds the most to the dependency graph — and the CLI is here to show
  that a driving adapter is a thin translation layer, which is easier to see when there is no
  framework doing the translating.
- **Click.** Mature, composable, and the decorator style hides where argv becomes a command —
  which is the single thing this adapter exists to demonstrate.
- **A `--as` default of `$USER`, or an `ACADEMY_CLI_ACTOR` variable.** Convenient, and both make
  the actor invisible at the call site. An environment variable is worse than a flag here for the
  same reason it is better for configuration: it does not appear in the command someone reads
  back later.
- **A real login: prompt for a password, verify a hash.** There is no password anywhere in this
  system to verify — `Person` has an `Email` and no secret — so this would mean inventing an
  authentication store for a tool whose caller already holds the database credentials.
- **Keep ADR-0010 as written and send a token to nothing.** Consistent on paper, and it would
  have put a header parser in a process with no headers.
