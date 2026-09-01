"""The actor identity a deployment with **no people in it yet** uses.

This is not a test double and it does not live in ``tests/`` (ADR-0014). It answers a real
problem that :class:`~academy.adapters.outbound.identity.repository.RepositoryActorIdentity`
cannot: a freshly migrated database has no rows, so there is no person to read, so nobody
resolves, so nobody can reach the administrative surface that would create the first person.
The system is locked out of itself on day one.

The usual industry answers are a seeded superuser row and a break-glass flag. A seed row is
worse than this: it is indistinguishable from a real person, it survives long after bootstrap,
and it is how default credentials end up in production. A configured id that resolves to an
administrator and touches no storage is smaller, is visible in ``env | grep ACADEMY_``, and
disappears the moment the deployment stops asking for it.

Two consequences that belong on the deployment's mind rather than buried here:

* **Whoever holds the id is an administrator.** There is no credential check behind it beyond
  the session signing every other actor gets, so the id is a secret in the way a password is.
* **It is meant to be turned off.** ``ACADEMY_IDENTITY=repository`` is the default; a deployment
  that leaves it on ``static`` after creating its first real administrator has kept a key under
  the mat.
"""

from __future__ import annotations

from collections.abc import Mapping

from academy.application.dtos import Actor
from academy.domain.shared.ids import PersonId


class StaticActorIdentity:
    """Resolves an actor from a fixed table, without touching storage.

    Satisfies :class:`~academy.application.ports.outbound.identity.ActorIdentity`.
    """

    def __init__(self, actors: Mapping[PersonId, Actor]) -> None:
        """Build the identity over the actors this deployment configured.

        Args:
            actors: The whole population, by id. A ``Mapping`` rather than a ``dict`` because
                this adapter only ever reads it, and because the composition root should be
                free to hand over something it does not want mutated.
        """
        self._actors = actors

    async def resolve(self, person_id: PersonId) -> Actor | None:
        """Look the actor up in the configured table.

        ``async`` although nothing here awaits, because the *port* is async (ADR-0005) and the
        port is async for the implementation that does I/O. An adapter does not get to narrow
        its port's contract just because it happens to be fast.

        Args:
            person_id: The id authentication established.

        Returns:
            The configured actor, or ``None`` if this deployment configured no such id --
            which is the same answer, and carries the same meaning, as an unknown person id
            against the repository-backed identity.
        """
        return self._actors.get(person_id)
