"""The actor identity a deployment with people in it uses: read the person, take their roles.

The port's whole contract is "given an already-authenticated person id, who are they and what do
they hold?" (ADR-0010), and for a system whose people live in a database the answer is a lookup.
There is nothing clever here, and that is the point: the interesting part of ADR-0010's claim is
that this returns the *same* :class:`~academy.application.dtos.Actor` whether a session cookie or
a bearer token produced the id, so nothing downstream can tell them apart.

Roles are read on every call and never cached. That is a requirement of the port, not an
implementation choice -- see the state diagram in ``docs/04-state-diagrams.md`` §7. Caching them
in the session would mean a teacher removed from the staff at 10:00 keeps write access until
their cookie happens to expire.
"""

from __future__ import annotations

from academy.application.dtos import Actor
from academy.application.ports.outbound.repositories import PersonRepository
from academy.domain.shared.ids import PersonId


class RepositoryActorIdentity:
    """Resolves an actor by reading the person record.

    Satisfies :class:`~academy.application.ports.outbound.identity.ActorIdentity`.
    """

    def __init__(self, people: PersonRepository) -> None:
        """Build the identity over a person repository.

        Args:
            people: The repository to read from. Used only to read -- resolving an identity
                never writes, which is why no unit of work is taken.
        """
        self._people = people

    async def resolve(self, person_id: PersonId) -> Actor | None:
        """Build the actor for this person, with the roles they hold right now.

        Args:
            person_id: The id authentication established. It is *already* trusted: verifying
                the credential that produced it happened at the adapter edge, and this method
                would be a security hole if it were ever passed a value out of a request body.

        Returns:
            The actor, or ``None`` if no such person exists.

            ``None`` and ``Actor(person_id, roles=frozenset())`` are different answers and the
            difference matters: a valid session naming a deleted person is *unauthenticated*
            and should not reach a page at all, while an actor with no roles is a real person
            who is simply allowed to do very little.
        """
        person = await self._people.get(person_id)
        if person is None:
            return None
        return Actor(person_id=person.id, roles=person.roles)
