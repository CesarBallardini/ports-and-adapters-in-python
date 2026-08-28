# ADR-0014 — In-memory adapters are first-class, verified by contract tests

- **Status** Accepted
- **Date** 2026-08-28

## Context

Ports are `Protocol`s, so conformance is structural: an adapter satisfies a port by having the
right method names and types. Nothing checks that it has the right *behaviour*. A repository
that returns `None` where the port's docstring says "raises `NotFoundError`" type-checks
perfectly and breaks a use case at runtime.

That risk grows with a second implementation, and this repository has two of nearly everything —
which is the point of ADR-0008 and of the in-memory adapters used throughout the unit tier.

## Decision

Treat the in-memory adapters as **production-grade adapters**, not test doubles, and verify every
implementation of a port against a **single shared contract test suite**, parametrised over all
implementations.

The port docstring is the specification, in enough detail to be testable: what `get` returns for
an absent id, what `save` does to an unknown entity, what ordering `list_all` guarantees. The
contract suite asserts exactly those statements, and it runs against the in-memory adapter and
the SQLAlchemy adapter in the same session.

When the two disagree, the contract suite is the arbiter: the specification decides which
behaviour is correct, and the other adapter is the bug.

## Consequences

- The in-memory adapters are trustworthy enough to be the substrate for the unit and BDD tiers,
  which is what keeps most of the suite fast.
- Adding a third adapter means adding it to one parameter list; if it does not comply, the
  failure is immediate and specific.
- Port docstrings must be written as specifications rather than descriptions. That is more work,
  and it is the work that makes the port an abstraction rather than a shape.
- Some contract assertions are awkward to state for both backends at once — transaction
  visibility especially — and a few end up backend-conditional, with the reason recorded.

## Alternatives considered

- **In-memory fakes living in `tests/`.** The common approach, and it makes them second-class:
  they drift, and nothing verifies them against the real thing.
- **`runtime_checkable` protocol checks only.** Confirms the methods exist and says nothing at
  all about what they do.
- **Testing against the real database everywhere.** No fidelity gap, and a suite slow enough that
  people stop running it.
