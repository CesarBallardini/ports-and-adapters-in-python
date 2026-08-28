# ADR-0012 — One declarative DomainError to HTTP status table

- **Status** Accepted
- **Date** 2026-08-28

## Context

Two inbound adapters — web and API — must turn the same `DomainError` into the same outcome. The
obvious implementation, a `try/except` in each handler, is where this goes wrong: the reference
application accumulated roughly thirty hand-written translation sites, and they did not agree
with each other.

The failure mode is quiet. A missed `except` becomes a 500 with a stack trace, on a path where
the domain behaved exactly as designed.

## Decision

One declarative mapping in `adapters/inbound/error_status.py`, consulted by both adapters
through a shared exception handler:

| Error | Status |
|-------|--------|
| `ValidationError`, and every `InvalidTitleError`-style value-object failure | 422 |
| `NotFoundError` | 404 |
| `ConflictError`, `DuplicateTaskError`, `AlreadyEnrolledError` | 409 |
| `AuthorizationError` | 403 |
| `PayloadTooLargeError` | 413 |
| any other `DomainError` | 400 |

The web adapter renders the mapped status as an HTML fragment; the API renders it as a JSON
problem document. Handlers contain **no** `except DomainError`. Anything that is not a
`DomainError` is a genuine bug and is allowed to become a 500.

## Consequences

- The two surfaces cannot disagree about what a conflict is.
- A new domain error is registered in one place, and both adapters honour it immediately.
- Use cases raise domain errors and never think about HTTP, which keeps `application` free of
  `fastapi` — a property ADR-0004 enforces.
- The table is a coupling point between the domain's exception taxonomy and HTTP semantics, so
  it needs review when either changes.
- Genuinely exceptional cases still need a per-route decision; the table covers the common ones,
  not every one.

## Alternatives considered

- **`try/except` per handler.** Maximum local control, and demonstrably drifts.
- **Domain exceptions carrying their own status code.** Removes the table at the cost of putting
  HTTP inside the domain, which is exactly backwards.
- **A FastAPI exception handler only.** Works for the API; gives the web adapter no way to render
  a fragment instead of a JSON body.
