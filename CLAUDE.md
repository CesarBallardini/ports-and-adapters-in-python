# Project guidance for ports-and-adapters-in-python

**academy**, an academic-records backend, built as a worked example of **ports and adapters**.
It is the second half of a diptych with `localenv-python` (tooling) and the continuation of
`multi-tenant-python`'s roadmap.

Read `docs/` in numbered order: description → use cases → sequence diagrams → state diagrams →
domain model → class diagram. Decisions live in `docs/decisions/` as ADR-0001..0014, and are
the first place to look before changing anything structural.

## The one rule that outranks the others

**`src/academy/domain/` is copied verbatim from `multi-tenant-python` and must not be
modified** (ADR-0002). It is the whole point of the repository: the hexagon is grown around a
domain that was written without knowing about it.

Verify with:

```bash
diff -r src/academy/domain ../multi-tenant-python/src/academy/domain -x '__pycache__'
diff -r tests/unit ../multi-tenant-python/tests/unit -x '__pycache__'
```

Both must print nothing. If persistence, HTTP or an import appears to require a domain change,
that is a leak in the new layer -- fix the layer. The only exception is a genuine domain bug,
which is fixed in **both** repositories.

This also constrains tooling: **do not add a lint rule the copied domain does not satisfy.**
`ruff.toml` stays in step with `multi-tenant-python`'s for exactly this reason. A rule the
copied tree *does* satisfy may be added — run it over `src/academy/domain` first and confirm
zero findings, as was done for `C90`. (`TC` / `flake8-type-checking` fails that test with 120
findings and must stay out; see the next section. `ruff.toml` records both.)

It constrains the test tree too: `tests/unit/` is copied verbatim as well, so **never add a
file to it** — a new file makes the diff above non-empty. Application-layer unit tests live in
`tests/application/` and carry the same `unit` marker; the tier is the marker, not the
directory.

## Development workflow

Build in phases, inside-out: **domain → ports → use cases → adapters → composition root**.
After each phase, apply the standing reviews and keep the gate green.

### Rule 1 — Per-phase test hunt

At the end of every phase, deliberately look for tests to add across **all tiers** — unit,
integration, acceptance, e2e — not only the tier the phase was about. Write the ones that carry
their weight.

`make test-bdd` and `make test-e2e` carry an `ALLOW_EMPTY_TIER` guard, because pytest exits 5
when a marker selects nothing and those tiers are unwritten. **Delete the guard from a target
the moment that tier gets its first test**, or an empty tier goes back to passing silently.

### Rule 2 — Post-test typing review

Review the phase's code for **typing opportunities**. Prefer precise, named types over loose
structures: `NewType`, dataclasses, `NamedTuple`, `TypedDict`, enums, `Protocol`.

**`Any` and `object` as annotations are a red flag** — every occurrence must be justified or
replaced.

### Rule 3 — Ports are specifications

A port docstring is what the contract test suite asserts (ADR-0014), so write it as a
specification, not a description: what a lookup returns when absent, what an update raises,
what ordering is guaranteed, which library exceptions the adapter must normalise. If a
docstring cannot be turned into an assertion, it is not finished.

Every port states **sync or async, and why** (ADR-0005): async when crossing it means waiting
on something outside the process, sync when it does not.

### Rule 4 — Two implementations, one contract suite

**A port with one implementation has never been tested as an abstraction.** Every port gets at
least two adapters — in-memory *and* SQLAlchemy, CSV *and* XLSX, local *and* S3 — and all of
them are parametrised through a single shared contract test. In-memory adapters are
production-grade adapters, not test doubles, and they live in `src/`, not in `tests/`.

### Rule 5 — Quality gate per phase

All of these must be green before a phase is done:

```bash
make lint types arch test coverage security docs
```

That is ruff (lint + format), pyright + pyrefly, **import-linter**, the full test suite, the
coverage floor in `.coveragerc`, bandit + pip-audit + OSV-Scanner + gitleaks + pip-licenses,
and a `--strict` MkDocs build.

`make precommit` runs every hook at once and is what CI runs, so a green local run and a green
CI run cannot drift. Two of the security tools are Go binaries that uv cannot install —
OSV-Scanner and gitleaks must be on `PATH` for `make security` (the pre-commit hook and the CI
job manage their own gitleaks).

## Conventions

- **Dependency rule:** `adapters → application → domain`; `config` may know everything. Enforced
  by import-linter in pre-commit *and* CI (ADR-0004). A second contract bans framework imports
  from `domain` and `application` — the layering alone does not catch `import sqlalchemy` in a
  domain module.
- **No quoted or deferred imports.** Normal top-level imports everywhere; no `if TYPE_CHECKING:`
  blocks and no string annotations. `flake8-type-checking` is intentionally disabled. This is
  load-bearing where names are subscripted in class bases (`Repository[Person, PersonId]`), which
  are runtime expressions that `from __future__ import annotations` does not defer.
- **Pure domain:** no clock, randomness or I/O in `domain/`. Time enters as an explicit `today`
  argument; clock and ids are ports.
- **Authorization is self-served and split in two:** the pure `AccessPolicy` (domain) decides
  what a relation grants; `RelationshipResolver` (application) discovers which relations hold,
  because that needs I/O. `AccessGuard` joins them so no use case repeats the dance.
- **Use cases return DTOs, never domain entities.** An adapter must not be able to invoke domain
  behaviour, and a Jinja2 template must not be able to cause a side effect.
- **One implementation class per port, not per use case.** Use cases are methods; a large one
  delegates to a collaborator (`ImportService` → `RowImporter`) rather than growing.
- **Domain errors are translated once**, by the declarative table in
  `adapters/inbound/error_status.py` (ADR-0012). Routers contain no `except DomainError`.
- **US spelling**, matching the domain's own: `enroll`, `enrollment`.
- **English** in code, comments, docstrings and docs.
- **Mermaid** for all diagrams, inline in markdown.
- **Do not reference dated working notes** (`docs/YYYY-MM-DD-*.md`) from committed code or docs.
  Committed files reference only durable committed files.

## Things that bite

- `pytest` runs with `asyncio_mode = "auto"`; async tests need no decorator.
- **Warnings are errors** (`filterwarnings = ["error", ...]`). A new ignore must name a
  *specific message*, never a bare category, and say why it cannot simply be fixed. The three
  present are all pytest-bdd/gherkin lag.
- `uv sync` needs `--all-extras` as well as `--all-groups`: adapter dependencies are **extras**,
  because the hexagon core deliberately has none. The same applies to `uv export` in the licence
  gate and to the docs job — without it, nothing distributed gets checked and mkdocstrings
  cannot import the adapters.
- **MkDocs runs `strict: true`, so every page under `docs/` must appear in `mkdocs.yml`'s nav.**
  Writing ADR-0015 and not adding it there turns `make docs` red. That is deliberate: it is what
  stops a decision being written and then never linked.
- There is no system Python on this machine. Always `uv run --frozen python`, never `python`.
- Very large heredocs get truncated by the shell tooling here; write long files with the editor
  tool instead of `cat <<'EOF'`.
- `make install` runs `pre-commit install`, which needs `.git` to exist. **Re-run it whenever
  `default_install_hook_types` changes** — a newly added stage (`commit-msg`, `pre-push`) is not
  wired into `.git/hooks` until you do, and the hook then silently never fires.
- `.markdown-link-check.json` carries entries meant to be deleted, each labelled: the
  `scorecard.yaml` badge goes once that workflow has run on the default branch. Link checking is
  **not** in the PR gate — it is a local hook plus the scheduled `links.yml` — because third-party
  availability is not deterministic and a random red build teaches people to bypass the check.

## Git

**Never mutate the repository.** No `add`, `commit`, `push`, `checkout -b`, `merge`, `rebase`,
`reset`, `tag`. Read-only inspection (`status`, `log`, `diff`, `show`) is fine. Make the edits,
then hand over the exact commands to run.

Commit messages follow **[Conventional Commits](https://www.conventionalcommits.org/)** —
`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`. A `commit-msg` hook checks
this locally and `cz check` checks the PR title in CI, because a squash merge takes the title
as the subject and `cz bump` reads that history to pick the next version. So any commit
command handed over must have a Conventional Commits subject.

The version lives **only in the git tag** (`uv-dynamic-versioning`); there is nothing to bump
in a tracked file. Commits to the default branch are blocked by a hook: work goes on a branch.

Branches are named `<type>/<slug>`, where `<type>` is the Conventional Commits type of the work
— `feat/quality-gates`, `fix/gitleaks-scan-scope`. A `pre-push` hook enforces it. So a handover
that starts a new line of work leads with `git checkout -b <type>/<slug>`.

**The pull request title has to be set by hand.** When a branch carries more than one commit,
GitHub defaults the title to the humanised branch name (`ci/sync-upstream-quality-gates` →
`Ci/sync upstream quality gates`), which drops the colon and fails `validate-pr-title`. The
`pre-push` hook warns and suggests a starting point. That title is what a squash merge records
and what `cz bump` reads, so it is the one that matters.
