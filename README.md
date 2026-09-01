# ports-and-adapters-in-python — academy

[![check](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/check.yml/badge.svg)](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/check.yml)
[![pytest](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/pytest.yml/badge.svg)](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/pytest.yml)
[![security](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/security.yml/badge.svg)](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/security.yml)
[![scorecard](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/scorecard.yaml/badge.svg)](https://github.com/CesarBallardini/ports-and-adapters-in-python/actions/workflows/scorecard.yaml)

A complete application built with **ports and adapters** (hexagonal architecture) in Python:
an academic-records backend with an htmx web UI, a JSON API, a CLI, bulk spreadsheet
ingestion, and two interchangeable implementations behind every port.

It is the second half of a diptych. [`localenv-python`](https://github.com/CesarBallardini/localenv-python)
covers the tooling a Python backend needs before the first route is written; this repository
covers how to organise the code once you start writing them.

## The point of the repository

Most hexagonal-architecture examples are a to-do list with a repository interface. The
abstraction never has to survive anything, so it never demonstrates what it is for.

This one starts from a domain that already exists and was written without any of this in mind —
the pure domain layer of [`multi-tenant-python`](https://github.com/CesarBallardini/multi-tenant-python),
about 1,270 lines with 1,863 lines of tests — and grows the hexagon around it **without changing
a single line of it**. That constraint is the whole exercise, and it is checked mechanically
rather than asserted (see [ADR-0002](./docs/decisions/0002-reuse-the-academy-domain.md)).

Concretely, the repository tries to demonstrate three claims:

1. **A port with one implementation has never been tested as an abstraction.** So every port
   here has at least two: in-memory *and* SQLAlchemy, CSV *and* XLSX, local disk *and* S3,
   system clock *and* fixed clock — all verified against one shared contract test suite.
2. **Business rules belong above the port, not in the adapter.** The bulk-import use case keeps
   header normalisation, deduplication, per-row validation and dry-run; the adapter only turns
   bytes into rows. The acceptance suite runs the same Gherkin scenarios against both adapters
   and requires identical outcomes, so a rule leaking downward fails the build.
3. **A use case should not know who called it.** The same import object is driven by an HTTP
   upload, a JSON API call, a CLI command and a background worker.

It also tries to be honest about the costs, which are real: more indirection, a large volume of
near-identical CRUD templates, and a composition root that grows into a maintenance surface of
its own.

## Domain

**academy** is a multi-tenant academic-records system — students, teachers, guardians and
administrative staff, with degree programs, study plans, subjects, course sections, grades and
graduation. Its distinguishing feature is **relationship-based authorization**: each person is a
tenant, and access to another person's records flows along relationships (*self*,
*teacher-of-section*, *guardian-of*, *administrator*) rather than through roles in a shared
tenant. A student reads only their own grades; a teacher writes only for students in a section
they teach; a guardian reads only their wards', and only until the ward comes of age.

Full specification in [`docs/01-description.md`](./docs/01-description.md).

## Documentation

Written in the order it should be read, and derived rather than decreed — use cases give the
behaviour, sequence diagrams assign the responsibilities, the class diagram is what falls out.

| # | Document | Question it answers |
|---|----------|---------------------|
| 01 | [Description](./docs/01-description.md) | What problem does the system solve, under what rules? |
| 02 | [Actors and use cases](./docs/02-actors-and-use-cases.md) | Who uses it, and what for? |
| 03 | [Sequence diagrams](./docs/03-sequence-diagrams.md) | For each use case, which object does what? |
| 04 | [State diagrams](./docs/04-state-diagrams.md) | What lifecycles exist, and what is stored versus computed? |
| 05 | [Domain model](./docs/05-domain-model.md) | What are the entities, value objects and invariants? |
| 06 | [Class diagram](./docs/06-class-diagram.md) | What classes result, in which layer? |
| — | [Decisions](./docs/decisions/) | 20 ADRs: why each choice was made, and what was rejected |

## Architecture

Four layers, dependencies pointing strictly inward
([ADR-0003](./docs/decisions/0003-ports-and-adapters-layering.md)):

```
src/academy/
  domain/          pure. entities, value objects, domain services, AccessPolicy.
                   imports nothing. copied verbatim, never modified.
  application/     use cases, DTOs, RelationshipResolver
    ports/inbound/   what the world may ask of us, grouped by actor intent
    ports/outbound/  what we need from the world, as typing.Protocol
  adapters/
    inbound/       web (FastAPI + Jinja2 + htmx), api (JSON), cli, jobs
    outbound/      persistence (in-memory, SQLAlchemy), spreadsheet (csv, openpyxl),
                   storage (local, S3), email, system (clock, ids)
  config/          Settings and the composition root -- the only module
                   allowed to know both a port and its adapter
```

The rule is enforced by **import-linter** in pre-commit *and* as a blocking CI check, with a
second contract banning framework imports from the inner layers — because a domain module that
imports no sibling layer but does `import sqlalchemy` satisfies the layering and still destroys
the property the layering was for ([ADR-0004](./docs/decisions/0004-enforce-the-dependency-rule.md)).

Ports are async when crossing them means waiting on something outside the process, and sync when
it does not ([ADR-0005](./docs/decisions/0005-async-io-ports-sync-cpu-ports.md)).

## Status

Honest state of the work, since this is a repository under construction:

| Part | State |
|------|-------|
| Tooling scaffold — uv, ruff, pyright, pyrefly, bandit, pre-commit, CI | done, green |
| Domain layer + its unit tests, copied verbatim | done, green |
| Design documentation — 01 to 06 | done |
| 22 ADRs | done |
| Application layer — driven ports, driving ports, DTOs, commands, authorization | done, green |
| Use cases — grading, student records, bulk import | done, green |
| Outbound adapters — in-memory, SQLAlchemy + Alembic, csv/openpyxl, storage, queue | done, green |
| Composition root — settings, container, per-request scope | done, green |
| Inbound adapter — CLI (`python -m academy`) | done, green |
| Inbound adapter — htmx web + JSON API, over the grading use cases | done, green |
| Web + API routes for student records and bulk import | **not yet implemented** |
| Inbound adapter — background worker | **not yet implemented** |
| Remaining repositories — programs, subjects, credentials, graduations, plan enrollments | **not yet implemented** |

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/), Git, and — only for `make security` —
[OSV-Scanner](https://google.github.io/osv-scanner/) and
[gitleaks](https://github.com/gitleaks/gitleaks) on `PATH`. Both are Go binaries rather than
Python packages, which is why uv cannot install them; the pre-commit hook and the CI job each
manage their own copy of gitleaks, so only the standalone target needs it. Python 3.14 is
installed automatically by uv from `.python-version`.

```bash
git clone https://github.com/CesarBallardini/ports-and-adapters-in-python
cd ports-and-adapters-in-python
make install
make lint types arch test coverage security
```

Everything goes through the Makefile; `make` with no target lists every one.

### Running it

The CLI is the first driving adapter, and the smallest — no framework, no server, no extras
([ADR-0020](./docs/decisions/0020-argparse-cli-with-an-asserted-actor.md)):

```bash
python -m academy config show                     # every setting, defaults resolved
python -m academy grades list <SECTION> --as teacher@example.edu
python -m academy import template --format csv --section <SECTION> \
    --output grades.csv --as teacher@example.edu
python -m academy import run grades.csv --section <SECTION> --dry-run \
    --as teacher@example.edu
```

`--as` names the person to act as and the CLI trusts it: identity is **asserted, not
authenticated**, because anyone who can run the command already holds the database credentials.
Authorization is not bypassed — the same `AccessPolicy` that refuses a student over HTTP refuses
them here.

Two things are interfaces and the prose is not: `--json` on any command, and the exit status —
`0` ok, `2` usage, `3` validation, `4` not found, `5` conflict, `6` forbidden, `7` too large,
`8` rule, `9` configuration, `10` import incomplete
([ADR-0019](./docs/decisions/0019-one-failure-classification-rendered-per-adapter.md)). So
`import run --dry-run` is worth putting in CI: it exits non-zero when a row would be rejected.

The **web adapter** serves the browser UI and the JSON API over the *same* use cases
([ADR-0011](./docs/decisions/0011-htmx-for-the-web-adapter.md),
[ADR-0021](./docs/decisions/0021-one-driving-port-per-route.md)):

```bash
make run                                          # uvicorn on :8000, in memory, no setup
```

Then sign in at `http://localhost:8000/sign-in` as any seeded person and open a section's grade
sheet. Recording a grade `hx-post`s the form and swaps back the one row that changed — that is
the whole of the htmx pattern, and the JSON API at `/api/sections/<id>/grades` reaches the same
`ManageGrades` port through a bearer token instead of a cookie. A test records the same grade
both ways and demands the identical outcome, which is what makes "one hexagon, four drivers" a
claim rather than a diagram.

**The credential check is a deliberate placeholder and is not fit to deploy**
([ADR-0010](./docs/decisions/0010-session-cookie-web-bearer-api.md),
[ADR-0022](./docs/decisions/0022-two-actor-identity-adapters-and-a-placeholder-credential-check.md)).
It finds a person by email and ignores the password. Everything around it is real — the session
is signed and expires, and authorization reads the person's roles fresh on every request — but
password hashing is out of scope for a repository about ports and adapters, and the module says
so in a banner you cannot miss.

Configuration is environment variables read once at startup, all prefixed `ACADEMY_`, so
`env | grep ACADEMY_` is a deployment's entire configuration. With none set it runs in memory.

### The gates

Each one fails the build rather than printing a warning.

| Gate | Command | Config |
| --- | --- | --- |
| Lint and format | `make lint` | `ruff.toml` |
| Types (two engines) | `make types` | `pyrightconfig.json`, `pyrefly.toml` |
| The dependency rule | `make arch` | `.importlinter` |
| Tests, by tier | `make test` | `pyproject.toml` |
| Coverage floor | `make coverage` | `.coveragerc` |
| SAST, CVEs, secrets, licenses | `make security` | `bandit.yaml`, `.gitleaks.toml`, `uv.lock` |
| Docs build (`--strict`) | `make docs` | `mkdocs.yml` |
| Every hook at once | `make precommit` | `.pre-commit-config.yaml` |

`make install` wires both the `pre-commit` and `commit-msg` hooks, so commit messages are
checked against [Conventional Commits](https://www.conventionalcommits.org/) locally — the
same check CI runs over a pull request title, since a squash merge takes the title as the
subject. The version is never written in a tracked file: it is derived from the newest git tag
(see [`CONTRIBUTING.md`](./CONTRIBUTING.md)).

## References

The design decisions in [`docs/decisions/`](./docs/decisions/) draw on the following. Where a
free authoritative version exists, it is linked.

### Ports and adapters

- Cockburn, Alistair. *Hexagonal Architecture*. 2005.
  <https://alistair.cockburn.us/hexagonal-architecture/> — the original article, and still the
  clearest statement of why the pattern is symmetric between driving and driven sides.
- Cockburn, Alistair, and Juan Manuel Garrido de Paz. *Hexagonal Architecture Explained*.
  Humans and Technology, 2024. ISBN 978-1-7375197-8-2. — the book-length treatment by the
  pattern's author, and the source of the "configurator" role that this repository's `config/`
  layer implements.
- Martin, Robert C. *Clean Architecture: A Craftsman's Guide to Software Structure and Design*.
  Prentice Hall, 2017. — the Dependency Rule, stated in its most quotable form.
- Palermo, Jeffrey. *The Onion Architecture*. 2008.
  <https://jeffreypalermo.com/2008/07/the-onion-architecture-part-1/>
- Graça, Herberto. *DDD, Hexagonal, Onion, Clean, CQRS… How I put it all together*. 2017.
  <https://herbertograca.com/2017/11/16/explicit-architecture-01-ddd-hexagonal-onion-clean-cqrs-how-i-put-it-all-together/>
  — the best single reconciliation of the competing vocabularies.

### Ports and adapters in Python specifically

- Percival, Harry, and Bob Gregory. *Architecture Patterns with Python: Enabling Test-Driven
  Development, Domain-Driven Design, and Event-Driven Microservices*. O'Reilly, 2020.
  ISBN 978-1-4920-5220-3. Free web edition: <https://www.cosmicpython.com/> — the closest prior
  art to this repository; the Repository, Unit of Work and Service Layer chapters in particular.

### Domain-driven design

- Evans, Eric. *Domain-Driven Design: Tackling Complexity in the Heart of Software*.
  Addison-Wesley, 2003. ISBN 978-0-321-12521-7. — aggregates, value objects, domain services,
  and the persistence-ignorance the imperative mapping in
  [ADR-0006](./docs/decisions/0006-sqlalchemy-imperative-mapping.md) exists to preserve.
- Vernon, Vaughn. *Implementing Domain-Driven Design*. Addison-Wesley, 2013.
  ISBN 978-0-321-83457-7. — aggregate design rules, and referencing other aggregates by identity.

### Analysis and design method

- Larman, Craig. *Applying UML and Patterns: An Introduction to Object-Oriented Analysis and
  Design and Iterative Development*. 3rd ed. Addison-Wesley, 2004. ISBN 978-0-13-148906-6. —
  GRASP, and the use-cases-to-sequence-diagrams-to-class-diagram sequence that
  [`docs/`](./docs/) follows literally.
- Cockburn, Alistair. *Writing Effective Use Cases*. Addison-Wesley, 2001. — the fully dressed
  use case template used in [`docs/02-actors-and-use-cases.md`](./docs/02-actors-and-use-cases.md) §3.
- Jacobson, Ivar, Ian Spence, and Kurt Bittner. *Use-Case 2.0: The Guide to Succeeding with Use
  Cases*. Ivar Jacobson International, 2011. <https://www.ivarjacobson.com/publications/> — use
  case slices as units of delivery.

### Testing

- Freeman, Steve, and Nat Pryce. *Growing Object-Oriented Software, Guided by Tests*.
  Addison-Wesley, 2009. — "mock roles, not objects", and the argument that ports are discovered
  by listening to the tests.
- Fowler, Martin. *Mocks Aren't Stubs*. 2007.
  <https://martinfowler.com/articles/mocksArentStubs.html> — the distinction behind
  [ADR-0014](./docs/decisions/0014-in-memory-adapters-and-contract-tests.md)'s preference for
  verified in-memory adapters over mocks.
- Fowler, Martin. *TestPyramid*. 2012. <https://martinfowler.com/bliki/TestPyramid.html>

### Practice

- Nygard, Michael. *Documenting Architecture Decisions*. 2011.
  <https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions> — the ADR format
  used throughout [`docs/decisions/`](./docs/decisions/).
- Fowler, Martin. *Presentation Domain Data Layering*. 2015.
  <https://martinfowler.com/bliki/PresentationDomainDataLayering.html>

### Tools

[uv](https://docs.astral.sh/uv/) ·
[ruff](https://docs.astral.sh/ruff/) ·
[pyright](https://microsoft.github.io/pyright/) ·
[pyrefly](https://pyrefly.org/) ·
[import-linter](https://import-linter.readthedocs.io/) ·
[pytest](https://docs.pytest.org/) ·
[pytest-bdd](https://pytest-bdd.readthedocs.io/) ·
[FastAPI](https://fastapi.tiangolo.com/) ·
[htmx](https://htmx.org/docs/) ·
[Jinja2](https://jinja.palletsprojects.com/) ·
[SQLAlchemy imperative mapping](https://docs.sqlalchemy.org/en/20/orm/mapping_styles.html#imperative-mapping) ·
[Alembic](https://alembic.sqlalchemy.org/) ·
[openpyxl](https://openpyxl.readthedocs.io/) ·
[bandit](https://bandit.readthedocs.io/) ·
[pip-audit](https://pypi.org/project/pip-audit/) ·
[OSV-Scanner](https://google.github.io/osv-scanner/) ·
[gitleaks](https://github.com/gitleaks/gitleaks) ·
[pip-licenses](https://pypi.org/project/pip-licenses/) ·
[coverage.py](https://coverage.readthedocs.io/) ·
[commitizen](https://commitizen-tools.github.io/commitizen/) ·
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/) ·
[mkdocstrings](https://mkdocstrings.github.io/) ·
[OpenSSF Scorecard](https://securityscorecards.dev/)

### Companion repositories

- [`localenv-python`](https://github.com/CesarBallardini/localenv-python) — the tooling half of
  the diptych: linting, type checking, tests by kind, and security, wired up from the first commit.
- [`multi-tenant-python`](https://github.com/CesarBallardini/multi-tenant-python) — where the
  academy domain comes from, and where the multi-tenant authorization model is worked out in
  full.

## License

MIT — see [LICENSE](LICENSE).
