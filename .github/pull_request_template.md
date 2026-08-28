## What this changes

<!-- What does this do, and why? Link the issue if there is one. -->

## Why this way

<!-- Anything a reviewer would otherwise have to reverse-engineer: an approach
     considered and dropped, a constraint that forced this shape, a trade-off. -->

## Checklist

- [ ] The PR title follows [Conventional Commits](https://www.conventionalcommits.org/) — it becomes the commit subject on a squash merge, and determines the next version.
- [ ] `make lint types test coverage security` passes locally.
- [ ] Tests cover the change; coverage stays at or above the floor in .coveragerc.
- [ ] Docs updated if behaviour, tooling or the developer workflow changed.
- [ ] No secret, credential or real hostname in the diff.

## Notes for the reviewer

<!-- Where to start, what to look at hardest, anything deliberately left out. -->
