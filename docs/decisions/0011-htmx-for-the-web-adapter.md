# ADR-0011 — htmx and Jinja2 for the web adapter

- **Status** Accepted
- **Date** 2026-08-28

## Context

The application needs a browser surface covering the full administrative feature set, alongside
the JSON API. A single-page framework would mean a second toolchain, a build step, and a second
model of the domain expressed in TypeScript — none of which teaches anything about ports and
adapters, and all of which would compete with it for the reader's attention.

`bluedoter-tng` uses server-rendered Jinja2 with htmx for partial updates, keeping component
frameworks to isolated islands where charts genuinely need them.

## Decision

Server-rendered **Jinja2** templates driven by **htmx**, with no build step and no npm.

- Full pages extend a single `base.html`.
- Mutations return **HTML fragments**, swapped by `hx-target` / `hx-swap`, following the
  row-replacement pattern: `hx-post` a form, get back the one `_row.html` that changed.
- The htmx contract is centralised **once**, in `adapters/inbound/web/rendering.py` — including
  htmx 2's rule that non-2xx responses are not swapped, which is otherwise re-derived, slightly
  differently, in every delete handler.
- Templates are dumb: they receive DTOs, never domain entities, so no template can invoke domain
  behaviour.

## Consequences

- One language, one toolchain, and the repository stays clonable without Node.
- The web adapter is a genuine second driving adapter over the same use cases, which is the
  point being demonstrated — the JSON API and the browser UI call identical objects.
- Fragment routes are testable as plain HTTP calls returning HTML, with no browser needed for
  most assertions.
- Full CRUD across every administrative entity is a large number of near-identical templates.
  This is a real cost, and it is the honest one: it shows that hexagonal architecture removes
  no UI work whatsoever.
- Rich client-side interaction would eventually need something more, and the island approach
  from the reference application is the intended escape hatch.

## Alternatives considered

- **A SPA framework.** Better for highly interactive screens; brings a second toolchain, a build
  pipeline, and a duplicated model, none of which serve this repository's purpose.
- **Server-rendered forms with no htmx.** No JavaScript at all, at the cost of a full page reload
  for every row edit — and it would drop fragment rendering, which is the most instructive part
  of the web adapter.
