# ADR-0003 — Ports and adapters with four layers

- **Status** Accepted
- **Date** 2026-08-28

## Context

The subject of the repository. The question is not *whether* to use ports and adapters, but
exactly which layers exist, what each may import, and where the boundary between "the
application" and "the outside" is drawn.

## Decision

Four layers, with dependencies pointing strictly inward:

| Layer | Contains | May import |
|-------|----------|------------|
| `domain` | entities, value objects, domain services, `AccessPolicy` | nothing |
| `application` | use cases, DTOs, `ports.inbound`, `ports.outbound`, `RelationshipResolver` | `domain` |
| `adapters` | inbound: web, api, cli, jobs. outbound: persistence, spreadsheet, storage, email, system | `application`, `domain` |
| `config` | `Settings`, the composition root | everything |

Ports are **owned by the inside**. `application/ports/outbound/` declares what the application
needs from the world as `typing.Protocol`, and adapters conform structurally — an adapter never
imports a port in order to subclass it. Driving ports in `application/ports/inbound/` declare
what the world may ask of the application, grouped by actor intent.

`config` is the only module permitted to know both a port and its adapter.

## Consequences

- The domain is testable with no infrastructure at all, which is what lets the 1,863 inherited
  lines of unit tests run in under a second.
- Adding a delivery mechanism — the CLI, the job worker — costs one adapter and touches nothing
  else. This repository demonstrates that three times over.
- There is real indirection to pay for: following one HTTP request end to end means opening a
  router, a use case, a port and an adapter. For an application this size that is a genuine
  cost, incurred deliberately for the ability to swap and test each piece.
- Structural conformance via `Protocol` means an adapter can drift out of compliance without a
  failing import. Type checking and contract tests are what catch it — see ADR-0004 and ADR-0014.

## Alternatives considered

- **Three layers, folding `config` into `adapters`.** Fewer moving parts, but the composition
  root is the one place that legitimately violates the dependency rule, and it deserves to be
  visible rather than hidden inside a layer that must not.
- **Abstract base classes instead of `Protocol`.** Conformance becomes explicit and checkable at
  import time, at the cost of making adapters inherit from the application — a dependency
  pointing the wrong way for anyone reading the import graph.
- **Repository interfaces in the domain**, as some DDD presentations do. Rejected: a repository
  is an I/O-shaped interface, and the domain layer is defined by having no I/O.
