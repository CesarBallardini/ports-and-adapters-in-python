"""Turning ``--as <email>`` into the actor every command carries.

Asserted, not authenticated (ADR-0020). The CLI's credential is the database URL: anyone who can
run it already holds the application role's password and could read and write every row with
``psql``, so a check here would guard a door in a wall that is not there. What it is *not* is a
bypass of authorization -- the actor this produces is refused by ``AccessGuard`` exactly where
any other driver's actor would be.

Roles come from the person record, read on this invocation. That is the whole reason this module
exists rather than a one-line ``Actor(PersonId.from_str(...))`` at the call site: an actor built
from an id alone has ``roles=frozenset()``, which is not a smaller actor but a *different* one,
and a CLI that built one would quietly strip an administrator of their role and then report the
resulting refusal as if the policy had spoken.
"""

from __future__ import annotations

from academy.application.dtos import Actor
from academy.application.errors import NotFoundError
from academy.application.ports.outbound.repositories import PersonRepository


async def actor_for(people: PersonRepository, email: str) -> Actor:
    """Build the actor the operator asked to act as.

    Args:
        people: The person repository, used only to read.
        email: What ``--as`` was given. Matched case-insensitively, because
            :meth:`~academy.application.ports.outbound.repositories.PersonRepository.by_email`
            promises that.

    Returns:
        The actor, carrying the person's roles as they stand right now.

    Raises:
        NotFoundError: If no person has that address. Deliberately an error and not an anonymous
            run: ``--as dana@exmaple.edu`` is a typo, and a CLI that answered it with "you are
            not allowed to do that" would send its operator hunting through the policy for a
            missing letter.
    """
    person = await people.by_email(email)
    if person is None:
        raise NotFoundError('person', email)
    return Actor(person_id=person.id, roles=person.roles)
