# ADR-0010 — Session cookie for the web, Bearer token for the API

- **Status** Accepted
- **Date** 2026-08-28
- **Extends** academy decision A-14

## Context

There are three inbound adapters — an htmx-driven browser UI, a JSON API, and a CLI — and they
have genuinely different authentication needs. Browsers have cookies and need CSRF protection;
scripts have neither and want a header. academy's A-14 specified Bearer tokens only, which was
right when there was no browser surface.

## Decision

Authenticate per adapter, resolve to one identity:

- **Web** — a signed, HTTP-only session cookie, plus CSRF protection on unsafe methods.
- **API and CLI** — `Authorization: Bearer <token>`.
- Both are resolved by an inbound-adapter-level `ActorIdentity` port into the same
  `Actor(person_id, roles)` value.

**No use case ever sees a cookie, a token or a header.** Authorization is decided downstream by
`AccessGuard`, from the resolved actor and the relationships that hold — never from the
authentication mechanism.

Credential verification itself remains a teaching placeholder, isolated at the adapter edge and
marked as such. Password hashing and token rotation are out of scope for this repository.

## Consequences

- Each surface uses the idiom its clients already speak.
- Adding a fourth surface means writing one `ActorIdentity` adapter and nothing else.
- The authentication mechanism is provably irrelevant to authorization, because the type that
  reaches the use cases carries no trace of it.
- Two authentication paths mean two sets of edge cases — expiry, CSRF, malformed headers — and
  both need tests.
- The placeholder credential check must be unmistakably labelled, or someone will ship it.

## Alternatives considered

- **Bearer only**, keeping A-14 as written. One code path, and it forces the browser UI to carry
  tokens in `hx-headers` — awkward, and it puts a bearer token where any XSS can read it.
- **Cookies only.** Fine for the browser, hostile to `curl` and to the CLI.
- **A real identity provider, OIDC.** The right answer for a production system, and a large
  amount of machinery that would dominate a repository about ports and adapters.
