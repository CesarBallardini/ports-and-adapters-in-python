# ADR-0006 — SQLAlchemy imperative mapping, Alembic for schema

- **Status** Accepted
- **Date** 2026-08-28

## Context

The domain classes are copied verbatim under ADR-0002 and must stay free of infrastructure
under ADR-0003 and ADR-0004. SQLAlchemy's usual declarative style requires entities to inherit
from a `Base` and to declare `Mapped[...]` columns — which would put an ORM import inside
`academy.domain` and fail the forbidden-imports contract on the first try.

## Decision

Use SQLAlchemy 2.0 **imperative (classical) mapping**. Tables are declared separately in
`adapters/outbound/persistence/orm/`, and bound to the untouched domain classes with
`registry.map_imperatively()` in `mappers/`. The domain never learns it is persistable.

Schema comes from **Alembic migrations**, not `metadata.create_all()`, including for the test
database — so the schema under test is the schema that will be deployed.

## Consequences

- The domain stays pure, and ADR-0002's "the domain did not change" claim survives contact with
  a database. This is the single most convincing demonstration in the repository.
- Aggregates keep their own invariants — private collections, no public setters — instead of
  being reshaped into whatever the ORM finds convenient to map.
- Imperative mapping is markedly less common than declarative, so it is less well documented and
  the type checkers need help in places. Some mapping code carries a targeted `# type: ignore`
  with a comment explaining why.
- Collections that the domain exposes as read-only tuples need explicit mapping to their private
  backing attributes, which is fiddly.

## Alternatives considered

- **Declarative mapping on the domain classes.** Far more familiar and much less code. Rejected
  outright: it puts SQLAlchemy in the domain, which is the thing this repository exists to avoid.
- **Separate persistence models plus hand-written translation.** Total isolation, and the domain
  need not even be mappable — at the cost of a second parallel model and two translation
  functions per aggregate. Reasonable in a large system; unjustified here, where imperative
  mapping achieves the same isolation without the duplication.
- **`create_all()` in tests, Alembic in production.** Faster tests, and the well-known failure
  mode of passing tests against a schema nobody will ever deploy.
