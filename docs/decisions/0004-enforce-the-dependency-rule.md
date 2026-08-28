# ADR-0004 — Enforce the dependency rule mechanically

- **Status** Accepted
- **Date** 2026-08-28

## Context

The layering in ADR-0003 is a claim about which way import arrows point. Such a claim is one
careless commit away from being false, and the failure is silent: nothing breaks the day an
adapter import appears in the domain. It simply becomes impossible, months later, to test the
domain without a database.

Both reference projects reached the same conclusion. `bluedoter-tng` runs **import-linter**
contracts in CI; `multi-tenant-python` records the same intent as its decision A-02.

## Decision

Enforce the rule with **import-linter**, configured in `.importlinter` with two contracts:

- a **layers** contract — `adapters` above `application` above `domain`;
- a **forbidden** contract — banning `fastapi`, `starlette`, `pydantic`, `sqlalchemy`,
  `openpyxl` and `httpx` from `academy.domain` and `academy.application`.

It runs in pre-commit and as a blocking step in the `check` CI workflow. Running it in both
places is deliberate: a hook can be skipped with `--no-verify`, a required check cannot.

The second contract exists because the first is not sufficient. A domain module that imports no
sibling layer but does `import sqlalchemy` satisfies the layers contract while destroying the
property the layering was for.

## Consequences

- A dependency-rule violation is a failed build with a file and a line number, not a code-review
  argument.
- The architecture stays true as the repository grows, which is what a teaching example must
  demonstrate rather than assert.
- One more tool in the toolchain, and one more configuration file to keep in step with the
  package layout.

## Alternatives considered

- **A hand-written AST checker.** Prototyped here, and it worked in about 150 lines. Dropped in
  favour of import-linter: the same guarantee, maintained by someone else, already used by both
  reference projects, and with better contract types than a bespoke script would grow.
- **Code review only.** Works until the reviewer is busy or the change is large.
- **Separate distribution packages per layer**, with real dependency metadata. The strongest
  possible enforcement, and far too much packaging ceremony for one application.
