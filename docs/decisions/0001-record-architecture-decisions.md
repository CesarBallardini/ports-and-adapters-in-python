# ADR-0001 — Record architecture decisions in ADRs

- **Status** Accepted
- **Date** 2026-08-28

## Context

This repository exists to *teach* an architecture, so the reasoning behind each structural
choice is part of the deliverable — arguably the larger part. A reader who sees only the code
learns what was built; a reader who sees why can disagree with it, which is the point.

The reference projects record decisions differently: `multi-tenant-python` keeps a decision-log
table inside `architecture.md`, `bluedoter-tng` keeps one file per decision under
`docs/decisions/`. A single table stops scaling once entries need more than a sentence of
justification, and it gives no place to record what was rejected.

## Decision

Record each significant decision as a numbered file in `docs/decisions/`, following Michael
Nygard's ADR format: **Context**, **Decision**, **Consequences**, and an explicit
**Alternatives considered** section.

Decisions are immutable once accepted. A decision that no longer holds is superseded by a new
ADR that references it, rather than edited in place — the record of having believed something
else is itself useful.

## Consequences

- Every ADR can be linked to from code comments and from the design documents, so the
  justification sits one click from the thing it justifies.
- The rejected options are written down, which stops a settled question from being reopened
  every few months on the same grounds.
- It is more ceremony than a table. Decisions too small to argue about should not get an ADR.

## Alternatives considered

- **A decision-log table**, as in `multi-tenant-python`. Compact and scannable, but there is
  nowhere to put the alternatives or the trade-off, which is most of the value here.
- **Nothing but commit messages.** The reasoning exists but is unfindable six months later.
