# ADR-0022 — Two `ActorIdentity` adapters, and a labelled placeholder credential check

- **Status** Accepted
- **Date** 2026-08-31
- **Implements** ADR-0010
- **Satisfies** ADR-0014's rule that every port gets at least two adapters

## Context

`ActorIdentity` has been written since the application layer was built and had **no
implementation**. ADR-0020 explained why the CLI did not write one: the port resolves an
*already-authenticated* person id, and `--as <email>` produces no such thing. The Rule 4 corollary
recorded in `CLAUDE.md` said it would wait for "the web adapter, which brings a real session and a
second caller in the same change". This is that change.

Two things have to be settled.

**First, how many adapters.** ADR-0014 says a port with one implementation has never been tested
as an abstraction. But `ActorIdentity`'s obvious implementation reads the `PersonRepository`, and
its variation appears to live a layer *down*, in the two persistence backends — which would make a
second adapter look like something written to satisfy a rule.

**Second, what verifies a credential.** ADR-0010 chose a signed session cookie for the browser and
a bearer token for the API, and scoped password hashing and token rotation out of this repository
deliberately. It also recorded the consequence: *"The placeholder credential check must be
unmistakably labelled, or someone will ship it."*

## Decision

### Two adapters, and the second one is not ceremony

- **`RepositoryActorIdentity`** reads the person and takes their current roles. What a running
  system uses.
- **`StaticActorIdentity`** resolves a configured id from a mapping, touching no storage.

The second answers a case the first genuinely **cannot**: a freshly migrated database has no
rows, so there is no person to read, so nobody resolves, so nobody can reach the administrative
surface that would create the first person. The system is locked out of itself on day one.

The usual answers are a seeded superuser row or a break-glass flag. A seed row is worse: it is
indistinguishable from a real person, it survives long after bootstrap, and it is how default
credentials reach production. A configured id that resolves to an administrator and touches no
storage is smaller, is visible in `env | grep ACADEMY_`, and disappears when the deployment stops
asking for it.

Both run through one contract suite, `tests/contract/test_actor_identity_contract.py`, which
asserts what the port's docstring specifies — including that roles are read **fresh on every
call**, which an adapter that snapshotted its input would fail.

### One configuration axis, independent of persistence

```
ACADEMY_IDENTITY = repository | static     # default: repository
ACADEMY_BOOTSTRAP_ADMIN = <person id>      # required by, and only by, `static`
```

Independent of `ACADEMY_PERSISTENCE` on purpose: which database is underneath says nothing about
how a person id becomes an actor, and a deployment that had to change both together is one where
the bootstrap case — a durable, migrated, empty database — could not exist. `static` is not the
default, so leaving it on is a visible choice rather than an oversight.

### The signing key is resolved lazily, and refused loudly

```
ACADEMY_SECRET_KEY = <key>
```

Unset with in-memory persistence: a random key per process. Nothing survives a restart there
anyway, and it is what makes `make run` and the test suite work with no environment at all.

Unset with a real database: `ConfigurationError`. A generated key differs between workers and
between restarts, so a signed-in user would be signed out by whichever worker answered next and
every deploy would log everyone out — both of which look like flaky sessions and point at nothing.

**Resolved on first access rather than at construction**, because not every driver has sessions to
sign. The CLI and the import worker never ask, and a deployment of either should not have to
invent a signing key to satisfy a check for a surface it does not run.

### The credential check is a placeholder, and says so three times

`verify_credentials` looks a person up by email and does not check a password — there is none to
check, since `Person` carries no credential and adding one would mean modifying the copied domain
(ADR-0002). It is labelled in a module banner (`NOT FIT TO DEPLOY`), in the function's own
docstring, and by a test that asserts both labels are still there. A test on a comment looks odd
until you consider what it protects: the label is the only thing between this and a deployment,
and a tidy-up that removed it would otherwise be invisible in review.

Everything *below* the sign-in step is real — the session is genuinely signed, genuinely expires,
carries an id and no roles, and authorization is genuinely enforced from the person record on
every request.

## Consequences

- ADR-0010's central claim becomes testable rather than asserted: a cookie and a bearer token
  resolve through the same port to the same `Actor`, and
  `tests/integration/test_web.py::test_both_doors_refuse_the_same_actor` shows the two are refused
  identically.
- Roles cannot be cached in a session, because the session carries only an id. An administrator
  demoted mid-session loses access on their next request, which an integration test asserts
  through the whole adapter chain.
- A deployment that leaves `ACADEMY_IDENTITY=static` on after creating its first real
  administrator has kept a key under the mat. The variable's name and the module's docstring say
  so; nothing enforces it, and nothing can.
- `StaticActorIdentity` is a real adapter in `src/`, not a test double, and is expected to survive
  into the deployment story rather than being deleted when the web adapter matures.
- Two authentication paths mean two sets of edge cases, as ADR-0010 predicted. Both have tests.

## Alternatives considered

- **One adapter, contract suite parametrised over the two persistence backends.** Honest about
  where the variation lives, and it leaves the bootstrap problem unsolved — which would surface
  as an undocumented seed script rather than as a decision.
- **A seeded superuser row in a migration.** Solves bootstrap and creates a permanent account
  indistinguishable from a real person. This is the mechanism behind most default-credential
  incidents.
- **Defer `ActorIdentity` again** and resolve the actor straight from the repository in
  `security.py`, as the CLI does. Smallest change; leaves ADR-0010's central claim untested and
  the port unimplemented for a third phase running.
- **Require `ACADEMY_SECRET_KEY` unconditionally.** Simpler rule, and it breaks `make run` with no
  environment and forces every CLI deployment to configure a key for a surface it does not serve.
