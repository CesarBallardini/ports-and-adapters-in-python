# Contributing

Thanks for taking a look. This repository is a worked example of ports and
adapters, so the bar it holds itself to is part of what it demonstrates — the
gates below are not bureaucracy, they are the argument.

## Getting set up

```bash
git clone https://github.com/CesarBallardini/ports-and-adapters-in-python
cd ports-and-adapters-in-python
make install
```

`make install` syncs the environment from the committed `uv.lock` and installs
the git hooks — both the `pre-commit` and the `commit-msg` ones. Run `make`
with no target to list everything available.

Note the sync uses `--all-extras` as well as `--all-groups`. The hexagon core
deliberately has no dependencies, so every adapter's library is an *extra*;
syncing groups alone leaves the adapters unable to import.

## The one rule that outranks the others

`src/academy/domain/` is copied verbatim from `multi-tenant-python` and **must
not be modified** (ADR-0002). The whole point is that the hexagon was grown
around a domain written without knowing about it. Verify with:

```bash
diff -r src/academy/domain ../multi-tenant-python/src/academy/domain -x '__pycache__'
diff -r tests/unit ../multi-tenant-python/tests/unit -x '__pycache__'
```

Both must print nothing. If persistence, HTTP or an import appears to require a
domain change, that is a leak in the new layer — fix the layer. This also
constrains tooling: **do not add a lint rule the copied domain does not
satisfy.** `ruff.toml` records `TC` (flake8-type-checking) as the standing
counter-example.

## The workflow

1. **Branch.** Commits to the default branch are blocked by a hook on purpose;
   work lands through a pull request so the CI, coverage and security gates get
   a chance to run first. Name the branch `<type>/<slug>`, where `<type>` is the
   Conventional Commits type of the work — `fix/gitleaks-scan-scope`,
   `feat/quality-gates`. A `pre-push` hook enforces this.
2. **Commit.** Messages follow
   [Conventional Commits](https://www.conventionalcommits.org/) — `feat:`,
   `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`. The `commit-msg`
   hook checks this locally, and CI checks the pull request title, because a
   squash merge takes the title as the commit subject.
3. **Check locally** before pushing:

   ```bash
   make lint types arch test coverage security
   ```

4. **Open the pull request.** Give it a Conventional Commits title — that is
   what determines the next version number.

   Set the title *deliberately*; do not accept the one GitHub proposes. When a
   branch has more than one commit, GitHub defaults the title to the humanised
   branch name (`fix/gitleaks-scan-scope` becomes `Fix/gitleaks scan scope`),
   which drops the colon and fails `validate-pr-title`. The `pre-push` hook
   warns about this and suggests a starting point.

## What has to pass

Every one of these fails the build rather than printing a warning:

| Gate | Command |
| --- | --- |
| Lint and format | `make lint` |
| Types (pyright + pyrefly) | `make types` |
| The dependency rule (import-linter) | `make arch` |
| Tests | `make test` |
| Coverage floor | `make coverage` |
| SAST, CVEs, secrets, licenses | `make security` |
| Docs build (`--strict`) | `make docs` |
| Every hook at once | `make precommit` |

CI runs the *same* pre-commit hooks you do (`pre-commit run --all-files`), so
a green local run and a green CI run cannot drift apart.

### About the dependency rule

`make arch` is the gate this repository exists for. Three import-linter
contracts hold: dependencies point inward only, neither `domain` nor
`application` imports a framework, and `domain` imports nothing of ours but
itself. A violation is a design bug, not a style issue — it runs as a hook
*and* as a required check, because a hook can be skipped with `--no-verify`
and a required check cannot (ADR-0004).

### About the coverage floor

`.coveragerc` sets `fail_under`. It is deliberately below measured coverage
while the hexagon is still being built outward, so a newly landed adapter does
not fail the build before its contract tests exist. If a change drops coverage
below the floor, the honest fix is a test, not a lower threshold. If you
genuinely believe the floor is wrong, say so in the pull request and change it
as its own commit, so the decision is visible in the history.

## Testing tiers

Each tier is defined by how much of the hexagon is real (ADR-0013), and every
test carries its marker:

| Tier | What is real | Command |
| --- | --- | --- |
| `unit` | domain, application, in-memory adapters | `make test-unit` |
| `integration` | real persistence, in-process ASGI | `make test-integration` |
| `bdd` | domain, application, in-memory adapters | `make test-bdd` |
| `e2e` | everything, over real sockets | `make test-e2e` |

`tests/unit/` holds the domain tests copied verbatim and must stay
byte-identical — application-layer unit tests live in `tests/application/`.

`make test-bdd` and `make test-e2e` currently tolerate an empty selection,
because those tiers are not written yet. Remove the `ALLOW_EMPTY_TIER` guard
from a target as soon as it has its first test.

## Releasing

There is no version number in a tracked file. `uv-dynamic-versioning` derives
the package version from the newest git tag, and the `release` workflow
(manual dispatch) uses `cz bump` to work out the next tag from the commit
history. Releasing is therefore just: merge, then run the workflow.

## Reporting a security issue

Please do not open a public issue — see [SECURITY.md](SECURITY.md).
