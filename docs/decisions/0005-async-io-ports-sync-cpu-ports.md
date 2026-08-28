# ADR-0005 — Async ports for I/O, sync ports for CPU

- **Status** Accepted
- **Date** 2026-08-28

## Context

Every port needs a call convention, and mixing conventions arbitrarily produces an API where
nobody can predict whether a call needs `await`. The two uniform answers — everything sync, or
everything async — are each wrong in a different way: sync blocks the event loop under an ASGI
server, and async forces `await` onto operations that never wait for anything.

`bluedoter-tng` resolves this per port and records the reason in each port's docstring:
*"Sync: evaluation is fast CPU work, not I/O"* against *"Async: storage is I/O"*.

## Decision

A port is **async when crossing it means waiting on something outside the process**, and
**sync when it does not**. Every port module states which it is, and why, in its docstring.

| Async | Sync |
|-------|------|
| repositories, `UnitOfWork` | `Clock` |
| `FileStorage` | `IdGenerator` |
| `EmailSender` | `SpreadsheetReader`, `SpreadsheetWriter` |
| `JobQueue`, `ActorIdentity` | `AccessPolicy` (domain, pure) |

Persistence uses async SQLAlchemy 2.0 with `aiosqlite` and `asyncpg`. Use cases are `async def`
because they orchestrate repositories.

## Consequences

- No blocking call sits on the event loop under uvicorn.
- The convention follows from the port's purpose, so `await` is never a guess.
- Parsing a spreadsheet stays honest: it is CPU-bound, an `async def` wrapper would deceive the
  reader into thinking it yields, and a genuinely large file belongs on the job queue
  (ADR-0009) rather than dressed up as concurrent.
- Async is contagious upward: the application layer and every inbound adapter become async, and
  the test suite needs `asyncio_mode = "auto"`. A reader unfamiliar with asyncio pays that cost
  on page one.
- Two conventions coexist, so the rule has to be stated — hence this ADR and the port docstrings.

## Alternatives considered

- **Sync everywhere.** Markedly easier to read, and defensible at this size. Rejected because it
  diverges from both reference projects and from the async test strategy academy already chose,
  and because it would block the event loop under an ASGI server.
- **Async everywhere.** One rule to remember, at the cost of `await clock.today()` and the loss
  of a genuinely useful distinction.
