# ADR-0002 — Reuse the academy domain unchanged

- **Status** Accepted
- **Date** 2026-08-28

## Context

This repository needs a domain rich enough that ports and adapters is a real answer to a real
problem, rather than a shape imposed on a to-do list. Inventing one is expensive and, worse,
tempting to bend so that the architecture looks good.

`multi-tenant-python` already contains **academy**: an academic-records domain of about 1,270
lines with 1,863 lines of unit tests, built domain-first, with a written specification and a
domain model document. Its README states that the application layer, ports, adapters and
composition root are deliberately left for a later iteration.

## Decision

Copy `src/academy/domain/` and its unit tests **verbatim**, and build the missing layers around
them. The domain is treated as read-only input: if adding persistence or HTTP appears to
require changing a domain class, that is evidence of a leak in the new layer, not a defect in
the domain. The one exception is a genuine domain bug, which would be fixed in both repositories.

## Consequences

- The interesting work starts immediately, at the boundary, which is what this repository is about.
- "The domain did not change" becomes a **verifiable claim**: a diff against the source
  repository must stay empty for `domain/`. That is far stronger than prose asserting that the
  architecture protects the core.
- The academic domain carries real complexity — relationship-based authorization, computed
  guardianship, grandfathered plans — so the ports are motivated rather than decorative.
- Two repositories now share code without sharing a package. Divergence is managed by hand; the
  domain is stable enough that this is acceptable.
- This repository is effectively the continuation of `multi-tenant-python`'s roadmap, and both
  READMEs say so, so a reader is not surprised.

## Alternatives considered

- **Invent a fresh teaching domain.** No coupling between repositories, but weeks of work to
  reach comparable richness, and a strong pull toward a domain shaped to flatter the architecture.
- **Reduce a copy of the client application's domain.** Closest to real work, but it is client
  code; only its patterns may be reused, not its model. Those patterns are captured in the other ADRs.
- **Import academy as a package dependency.** Avoids duplication, but a reader would have to
  navigate two repositories to follow one call chain.
