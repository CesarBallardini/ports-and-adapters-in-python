# Security Policy

## Reporting a vulnerability

Please report security issues **privately**, not as a public issue or pull
request.

Use GitHub's [private vulnerability
reporting](https://github.com/CesarBallardini/ports-and-adapters-in-python/security/advisories/new)
on this repository. If that is unavailable to you, contact the maintainer
through the address on the [GitHub profile](https://github.com/CesarBallardini).

Please include, as far as you can:

- what the issue is and which component it affects;
- the steps or a minimal case that reproduces it;
- the impact you think it has;
- the version or commit you observed it on.

You can expect an acknowledgement within **7 days** and an assessment within
**30 days**. If the report is accepted, the fix and a disclosure timeline get
agreed with you before anything is published; credit is given unless you would
rather it were not.

## Scope

This repository is a **worked example**, not a deployed service. The code in
`src/academy/` is an academic-records backend written to demonstrate ports and
adapters; it is not operated anywhere, and it stores nothing outside a local
database you point it at yourself.

That said, it does grow a real authentication and authorization surface as the
adapters land, and getting those wrong in an example is worse than getting them
wrong in a private service — an example gets copied. So in scope are:

- the authorization model: the `AccessPolicy` grant matrix, the
  `RelationshipResolver`, or an `AccessGuard` check a use case fails to make;
- the authentication adapters: session cookie for the web, bearer for the API
  (ADR-0010);
- the domain-error-to-HTTP-status table, where a leak would take the form of an
  error message distinguishing "no such record" from "not yours" (ADR-0012);
- a dependency in `uv.lock`;
- a GitHub Actions workflow, a composite action, or a pinned action digest;
- a pre-commit hook definition;
- a secret accidentally committed to the history.

## Supported versions

The newest release is the supported one. This is an example meant to be read
and copied from, so fixes go to the default branch and land in the next tag
rather than being backported.

## How this repository defends itself

| Concern | Tool |
| --- | --- |
| Vulnerable dependencies | `pip-audit` and OSV-Scanner, over `uv.lock` |
| Insecure code patterns | `bandit` |
| Committed secrets | `gitleaks`, as a hook and over the whole tree in CI |
| Dependency freshness | Dependabot, weekly, with a 10-day cooldown |
| Supply-chain posture | OpenSSF Scorecard |
| Workflow tampering | every action pinned to a commit digest; `permissions: {}` by default |
| License obligations | `pip-licenses`, gating the distributed dependencies |
