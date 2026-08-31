# Project guidance for ports-and-adapters-in-python

**academy**, an academic-records backend, built as a worked example of **ports and adapters**.
It is the second half of a diptych with `localenv-python` (tooling) and the continuation of
`multi-tenant-python`'s roadmap.

Read `docs/` in numbered order: description → use cases → sequence diagrams → state diagrams →
domain model → class diagram. Decisions live in `docs/decisions/` as ADR-0001..0020, and are
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

`ALLOW_EMPTY_TIER` is **gone**. It existed because pytest exits 5 when a marker selects nothing,
which was noise while a tier was unwritten; every tier now has tests, so an empty selection is a
signal again and each target says so. Do not reintroduce it.

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

The corollary is that a port with **no** implementation should not grow one speculatively.
`ActorIdentity` is the standing example: it resolves an already-authenticated person id, the CLI
has no authentication step to produce one (ADR-0020), and inventing an adapter for it now would
mean one implementation and no second caller to keep it honest. It waits for the web adapter,
which brings a real session and a second caller in the same change.

### Rule 5 — Quality gate per phase

All of these must be green before a phase is done:

```bash
make lint types arch test test-e2e coverage security docs
```

That is ruff (lint + format), pyright + pyrefly, **import-linter**, the full test suite, the
e2e tier, the coverage floor in `.coveragerc`, bandit + pip-audit + OSV-Scanner + gitleaks +
pip-licenses, and a `--strict` MkDocs build.

**`test-e2e` is listed separately because `make test` does not run it.** The marker excludes it
(`-m 'not e2e'`), so a phase could be called done with a broken entry point unless it is asked
for by name. CI runs it in its own `pytest-e2e` job for the same reason.

Raise the coverage floor as each phase closes, keeping a little slack — a floor set at exactly
today's number fails the moment a new adapter lands ahead of its contract tests.

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
- **Domain errors are classified once**, by the declarative table in
  `adapters/inbound/error_status.py` (ADR-0012), which produces a `Failure` and **not** a status
  code — each inbound adapter renders that in its own vocabulary (ADR-0019): an HTTP status, a
  CLI exit code, a worker's retry decision. Handlers contain no `except DomainError`. A new
  `ApplicationError` must be added to the table, and a test fails if it is not; a new
  `DomainError` falls through to `RULE` on purpose, because the domain is copied.
- **US spelling**, matching the domain's own: `enroll`, `enrollment`.
- **English** in code, comments, docstrings and docs.
- **Mermaid** for all diagrams, inline in markdown.
- **Do not reference dated working notes** (`docs/YYYY-MM-DD-*.md`) from committed code or docs.
  Committed files reference only durable committed files.

## Persistence

Two adapters, one contract suite, and a rule that shapes both.

- **The schema comes only from Alembic** (ADR-0006), from an empty database, in every
  environment including each test run. There is no `metadata.create_all()` anywhere and adding
  one would mean testing against a schema nobody deploys. `session.migrate_to_head(url)` is the
  entry point; it is synchronous because `env.py` drives its own event loop, so it must be
  called from a thread rather than from inside a running one.
- **The domain's value objects cannot be ORM-mapped.** Every one is
  `@dataclass(frozen=True, slots=True)`, and a class with `__slots__` and no `__weakref__` slot
  cannot be instrumented — `TypeError: cannot create weak reference`. Aggregate *roots* map
  fine, and value objects work as `composite`s or `TypeDecorator`s because neither is
  instrumented. The four internal collections are therefore JSON columns (ADR-0017), which is
  why `for_student`, `holders_of` and friends filter in Python.
- **`ImportJob` hits the same wall** and is application-owned, so a single `weakref_slot=True`
  would fix it. Deliberately not done: adding a slot for the ORM's benefit is the persistence
  layer reaching up a layer, and refusing that for the domain while accepting it here would be
  arguing two ways. That one repository maps by hand, over Core.
- **Two database roles** (ADR-0018): `ACADEMY_DATABASE_URL` for the application, which may
  change rows and not the schema, and `ACADEMY_MIGRATION_DATABASE_URL` for Alembic, which owns
  it. The application never migrates — with two roles it could not. Databases are named for
  their environment (`academy_production`, `academy_test`) and the schema is `academy`, selected
  per role with `search_path` rather than written into the table names, because a qualified
  `academy.people` is DDL SQLite cannot run.
- **A JSON collection must be named in `mutable_collections`, or its changes are lost.** A JSON
  column's change detection is by attribute *assignment*, and the domain mutates in place
  (`history.record(...)` appends). The ORM then compares the loaded value against the caller's
  value, finds the same mutated object, and emits no `UPDATE` — a `save` that returns cleanly, a
  `commit` that succeeds, and an unchanged row. `_SqlAlchemyRepository.save` calls
  `flag_modified` for every attribute a repository names there; a new serialised collection has
  to be added to that tuple. The bug is invisible to any test that reads back through the same
  session, because the identity map returns the very object that was mutated — which is why the
  contract suite's last six tests commit, drop the session, and ask again.

## Inbound adapters

One exists so far — the CLI — and it sets the shape the others follow.

- **Four modules along the seam that matters**: `parser.py` owns the grammar and imports no
  handler; `commands.py` owns the handlers and parses no argv; `render.py` owns the output and
  touches no domain object; `main.py` owns the control flow and holds **the one error boundary**.
  The two halves meet at a single dict from command name to handler, and a test asserts the join
  is total.
- **A handler takes one driving port, never the `Scope`.** A scope carries every repository as
  well as every use case, so a handler holding one could read a transcript straight out of
  `scope.histories` and never call a use case — a rule leaking into an adapter, one step away.
  `Command[PortT]` pairs a handler with the accessor for its port (`Command(Scope.student_records,
  records_show)`), checks the pairing once, and is itself a uniform `Handler` — so the table stays
  flat without an `Any`. A mispaired row does not type-check. Exactly two places may name a
  `Scope`: `Command.__call__` and `main._execute`.
- **`Args` is where `argparse`'s `Any` stops.** A `Namespace` attribute is `Any`, so reading one
  straight into a command object would lose exactly the checking two type checkers are run to
  get. Narrowing is a run-time check and not a cast, because the values genuinely arrive untyped.
- **`--as <email>` is asserted, not authenticated** (ADR-0020). The CLI's credential is the
  database URL, so a second one would guard a door in a missing wall. Authorization is untouched:
  the actor is refused wherever the policy refuses. Roles come from the person record on every
  invocation, never from an id alone.
- **Exit codes and `--json` are the interface; the prose is not.** A test may assert a code or a
  JSON key exactly, and should assert of a human line only what a person would complain about.
- Errors are never caught in a handler. `main` catches `ConfigurationError` (exit 9),
  `ApplicationError` and `DomainError` (the table decides), and lets everything else escape with
  its traceback — which exits 1, the same status `Failure`-less classification means.

## Things that bite

- `pytest` runs with `asyncio_mode = "auto"`; async tests need no decorator.
- **An `Actor` rebuilt from an id alone has no roles.** `Actor(person_id=...)` defaults `roles`
  to an empty frozenset, so it is not a smaller actor but a different one. Anywhere an actor is
  reconstructed from storage, read the person and take their *current* roles.
- **pytest-bdd never awaits an `async def` step.** It returns a coroutine, nobody runs it, and
  the assertion after it passes. Steps are sync and call `asyncio.run`.
- **A test written to expire should say so.** Two did — the SQLAlchemy-refused-at-startup pair —
  and both failed on exactly the day the adapter landed, which is the point. Write the comment
  that tells the next person to delete it rather than to fix it.
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
- **The e2e tier shares one database across the module, so only a read or a dry run is safe
  there.** `tests/e2e/` migrates and seeds once (`scope='module'`) because spawning an
  interpreter per test is already the expensive part. A test that *writes* would leak into every
  test after it, in an order pytest is free to change. `import run --dry-run` is safe by
  construction — the import happens in full and is rolled back — and that is why the two import
  e2e tests are dry runs. A writing e2e test needs its own database, not a new row in this one.
- **A contract test that never leaves its transaction tests answers, not writes.** The repository
  contract ran entirely inside one session for months and was passing while the SQLAlchemy
  adapter discarded every collection change. Anything asserting that something was *stored* has
  to commit and re-read; the `storage` fixture exists for that, and the `backend` fixture is for
  everything else.
- **`cz bump` needs `--yes`, and it is the prompt that dies under Git Bash, not `cz`.** With no
  tag yet it asks "Is this the first tag created?" through `prompt_toolkit`, which raises
  `NoConsoleScreenBufferError` there. `cz bump --get-next --yes` and `make release-next` are fine
  in any shell. Releasing is the `release.yaml` workflow, not a local bump: it resolves the
  version and pushes a tag, and there is no release commit.
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
this locally and `cz check` checks the PR title in CI, and `cz bump` reads that history to pick
the next version. So any commit command handed over must have a Conventional Commits subject.

**How a PR is merged changes which subject matters.** Under a *squash* merge the PR title
becomes the permanent commit subject, so it is the only one `cz bump` will ever read. Under a
*merge commit* — which is how PR #10 landed — every commit keeps its own subject and the merge
commit adds one more. Either way `validate-pr-title` still runs on the PR, so the title must be
conventional; what changes is whether a mislabelled commit inside the branch is survivable.

The version lives **only in the git tag** (`uv-dynamic-versioning`); there is nothing to bump
in a tracked file. Commits to the default branch are blocked by a hook: work goes on a branch.

Branches are named `<type>/<slug>`, where `<type>` is the Conventional Commits type of the work
— `feat/quality-gates`, `fix/gitleaks-scan-scope`. A `pre-push` hook enforces it. So a handover
that starts a new line of work leads with `git checkout -b <type>/<slug>`.

**The pull request title has to be set by hand — when the branch has more than one commit.**
GitHub then defaults it to the humanised branch name (`ci/sync-upstream-quality-gates` →
`Ci/sync upstream quality gates`), which drops the colon and fails `validate-pr-title`. The
`pre-push` hook warns and suggests a starting point; ignore the suggestion, which is derived
from the slug and says less than the branch does. A **single-commit** branch is the exception:
GitHub uses that commit's subject, which the `commit-msg` hook has already validated.
