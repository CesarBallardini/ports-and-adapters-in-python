# ADR-0019 — One failure classification, rendered per inbound adapter

- **Status** Accepted
- **Date** 2026-08-31
- **Extends** [ADR-0012](./0012-domain-error-to-http-status-table.md), which stands: one
  declarative table, consulted by every inbound adapter, and no `except DomainError` in a
  handler. This decides what the table produces, now that not every inbound adapter speaks
  HTTP.

## Context

ADR-0012 was written with two inbound adapters in view — the htmx web UI and the JSON API — and
both speak HTTP. A table from error class to status code was therefore the whole answer.

The CLI is the third, and it has no statuses. It has an **exit code**, which is what a shell
script, a `Makefile` and a CI job actually branch on. Nothing about `AuthorizationError` becomes
`403` when the caller is a terminal.

The two obvious ways out are both bad:

- **Import the HTTP table into the CLI** and translate `403 → 6` somewhere. HTTP semantics leak
  into a place that has no HTTP, and the second translation is a second table with none of the
  first one's protection.
- **Give the CLI its own table** from error class to exit code. This is exactly the shape
  ADR-0012 was written to prevent: two hand-maintained lists that agree today, and a new error
  registered in one of them tomorrow.

There is also a fourth inbound adapter coming — the worker — whose "rendering" is a job's
`failure_reason` and whose retry decision depends on the same question: *is this the caller's
fault or ours?*

## Decision

The table in `adapters/inbound/error_status.py` **classifies**; it does not render.

One enum, `Failure`, with a member per kind of expected failure, and one ordered table from
error class to member. Each inbound adapter renders a `Failure` in its own vocabulary:

| `Failure` | HTTP status | CLI exit code | Errors classified into it |
|---|---|---|---|
| `VALIDATION` | 422 | 3 | every `Invalid…Error` value-object failure, `MalformedSpreadsheetError` |
| `NOT_FOUND` | 404 | 4 | `NotFoundError`, `PlanNotFoundError` |
| `CONFLICT` | 409 | 5 | `ConflictError`, `AlreadyEnrolledError`, `Duplicate…Error`, `JobStateError`, `GraduationStateError` |
| `FORBIDDEN` | 403 | 6 | `AuthorizationError` |
| `TOO_LARGE` | 413 | 7 | `PayloadTooLargeError` |
| `RULE` | 400 | 8 | any other `DomainError` |

`classify` returns `None` for anything that is not one of these, and `None` means *a bug of
ours*: a 500 to an HTTP client, exit 1 to a shell. That is the same decision ADR-0012 made — an
unclassified exception is not quietly given a friendly status — expressed once instead of twice.

The table is **ordered, most specific first**, and matched by `isinstance`, so a subclass never
has to be remembered in two places.

Two rules keep it honest, and both are asserted by tests:

1. Every `ApplicationError` subclass is listed explicitly. There is no catch-all for that
   hierarchy, so a new one that nobody classified fails a test rather than becoming a 500 in
   production.
2. Every `DomainError` subclass classifies to something, via the `RULE` fallback. The fallback
   exists because the domain is copied and may grow a new error without this repository being
   consulted (ADR-0002); `400`/exit 8 is the right answer for a rule violation nobody has
   thought about yet.

## Consequences

- A new error is registered in one place and every inbound adapter honours it — the property
  ADR-0012 bought, kept while the number of adapters grows past two.
- The CLI never mentions an HTTP status, and `application` never mentions either rendering. The
  vocabulary in the middle — "this was forbidden" — is the only thing both agree on, and it is
  the one thing that is genuinely protocol-independent.
- The worker gets its answer for free: `classify(error) is None` is precisely "this is our bug",
  which is the retry-versus-record decision it has to make.
- The HTTP half of the table is written before the web adapter exists. That is not speculation —
  the mapping is fixed by ADR-0012 and is asserted directly against it — but it is code with no
  production caller until Phase C's second adapter lands.
- Exit codes 0 through 2 keep their conventional meanings (success, unexpected error, usage), so
  the classified failures start at 3. A CLI that returned 2 for "not found" would collide with
  argparse's own usage failure.

## Alternatives considered

- **Keep the HTTP table and let the CLI translate from it.** Fewer moving parts today, at the
  cost of HTTP status codes appearing in a terminal application and of a translation nobody
  writes a test for.
- **Errors carry their own classification.** Removes the table, and puts a presentation concern
  inside the exception — a smaller version of the "domain exceptions carrying their own status
  code" alternative ADR-0012 already rejected.
- **A separate table per adapter, checked against each other by a test.** Honest about the
  duplication and does catch drift, but pays for two tables and a third thing to maintain in
  order to get what one table gives outright.
