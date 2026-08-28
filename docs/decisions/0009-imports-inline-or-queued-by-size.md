# ADR-0009 — Imports run inline or queued, chosen by file size

- **Status** Accepted
- **Date** 2026-08-28

## Context

A teacher's grade sheet is thirty rows and should return a result immediately. A bulk person
import can be tens of thousands of rows and will outlast any sensible HTTP timeout. Forcing one
mechanism on both is wrong in one direction or the other: synchronous-only times out, and
job-only makes a thirty-row upload require polling to see three numbers.

## Decision

`SubmitImportJob` inspects the payload size and branches:

- **Below `import_inline_threshold_bytes`** — run the import use case inline and return the
  result in the response.
- **At or above it** — store the bytes through `FileStorage`, record a `PENDING` `ImportJob`,
  enqueue the id through the `JobQueue` port, and return the job id. The htmx UI polls the job
  fragment until it reaches a terminal state.

**Both arms call the same use case object.** The branch decides *where* the work runs, never
*what* it does. `JobQueue` has an inline adapter used in tests, which collapses the asynchronous
path into a synchronous one without changing a single assertion about the outcome.

## Consequences

- The common case stays a single request with no polling and no job table.
- The large case cannot time out, and its progress is visible.
- The rules, the result DTO and the tests are shared, so the two paths cannot drift apart.
- There is now a state machine to maintain (`docs/04-state-diagrams.md` §2) and a job record to
  store, plus a threshold that is a guess and will need tuning.
- Queued imports need a worker to be running, so a deployment has two processes rather than one.

## Alternatives considered

- **Inline only.** Simplest by a wide margin, and it breaks on exactly the files that make bulk
  loading worth having.
- **Queued only.** Uniform, and it makes every trivial upload asynchronous — worse UX, and it
  hides the fact that the choice is a deployment concern rather than a domain one.
- **Let the caller choose**, with a `?background=true` flag. Honest, and it pushes an operational
  decision onto someone with no basis for making it.
