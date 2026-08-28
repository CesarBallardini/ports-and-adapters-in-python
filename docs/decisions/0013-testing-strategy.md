# ADR-0013 — Testing strategy and tiers

- **Status** Accepted
- **Date** 2026-08-28

## Context

The tooling scaffold provides five pytest markers — `unit`, `integration`, `bdd`, `e2e`,
`snapshot` — without saying what belongs in each. In a ports-and-adapters codebase the layering
answers that question directly: each tier is defined by **how much of the hexagon is real**.

## Decision

| Tier | What is real | What is substituted | Speed |
|------|--------------|---------------------|-------|
| `unit` | domain, application | every outbound adapter, via in-memory adapters | milliseconds |
| `integration` | application, real persistence, in-process ASGI | network, browser | seconds |
| `bdd` | domain, application, in-memory adapters | infrastructure | fast |
| `e2e` | everything, over real sockets | nothing | slow, kept thin |

- **Unit** tests use the in-memory adapters, not mocks. A mock asserts that a call was made; an
  in-memory adapter asserts that the outcome was right, and it is verified against the same
  contract suite as the real one (ADR-0014).
- **Integration** tests drive the ASGI app in-process with `httpx.AsyncClient` and
  `ASGITransport`, against a migrated database — SQLite by default, PostgreSQL in CI (ADR-0007).
- **Acceptance** tests are Gherkin scenarios in domain language, and the import features run
  **parametrised over both spreadsheet adapters** to prove ADR-0008's claim.
- **e2e** tests exercise a real uvicorn over a real socket and the real CLI as a subprocess, and
  stay deliberately thin: enough to prove the wiring, not to re-test the rules.

`addopts` excludes `e2e` and `snapshot` by default, so `make test` is fast; CI runs `e2e`
explicitly.

## Consequences

- The bulk of the suite is the fast tier, because the architecture makes it possible to test
  rules with no infrastructure. That is the practical payoff of the layering.
- Every tier has an unambiguous home, so "where does this test go?" has an answer.
- The pyramid is enforced by the design rather than by discipline: writing a slow test for a
  pure rule requires deliberately going out of your way.
- Contract tests must be maintained alongside every repository port, or the in-memory adapters
  quietly stop being trustworthy.

## Alternatives considered

- **Mocks in unit tests.** Faster to write, and they assert interactions instead of behaviour,
  so they pass while the real adapter is broken.
- **Live server for all API tests.** Highest fidelity, far too slow to be the main tier, and it
  makes every test manage server lifecycle.
- **`fastapi.testclient.TestClient`.** A fine sync fallback, but it hides the async code paths
  that ADR-0005 deliberately introduced.
