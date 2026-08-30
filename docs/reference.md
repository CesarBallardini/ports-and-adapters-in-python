# API reference

Generated from the docstrings, which for this codebase is not a formality: a
**port docstring is the specification** its contract test suite asserts
(ADR-0014). What you read here is what the build checks.

The pages are ordered the way the hexagon is built — inside out.

## Domain

Copied verbatim from `multi-tenant-python` and not modified here (ADR-0002).
The point of the repository is that the hexagon was grown around a domain
written without knowing about it.

::: academy.domain.authorization.policy

::: academy.domain.services.grading_service

## Driving ports

Grouped by actor intent rather than one interface per use case, so a web
router that renders the grading screen cannot reach anything else.

::: academy.application.ports.inbound.grading

::: academy.application.ports.inbound.records

## Driven ports

All async, because persistence is I/O (ADR-0005). Read the docstrings as
specifications: what a lookup returns when absent, what an update raises, what
ordering is guaranteed.

::: academy.application.ports.outbound.repositories

::: academy.application.ports.outbound.unit_of_work

::: academy.application.ports.outbound.system

## Application

Use cases return DTOs, never domain entities, so an adapter cannot invoke
domain behaviour and a template cannot cause a side effect.

::: academy.application.grading

::: academy.application.records

::: academy.application.authorization

::: academy.application.dtos

## Adapters

Two implementations of every port, verified against one shared contract suite.
The in-memory ones are production-grade adapters, not test doubles.

::: academy.adapters.outbound.persistence.memory.store

::: academy.adapters.outbound.persistence.memory.repositories

::: academy.adapters.outbound.system.clock

## Composition root

The only code allowed to know both a port and the adapter behind it, wired by
hand rather than by a container (ADR-0015). Two lifetimes: the container is the
process, the scope is one request, command or job.

::: academy.config.settings

::: academy.config.container
