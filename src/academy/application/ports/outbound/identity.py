"""The actor-lookup port.

**Async** (ADR-0005): it reads persisted people.

Note carefully what this port is *not*. It does not verify credentials, and it knows nothing
about cookies, tokens or headers. Verifying a password or a signature is the inbound
adapter's job (ADR-0010); this port only answers "given that you have established a person
id, who are they and what roles do they hold?"

Keeping that line sharp is what makes the claim in ADR-0010 true: the authentication
mechanism is provably irrelevant to authorization, because the value that reaches the use
cases carries no trace of it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from academy.application.dtos import Actor
from academy.domain.shared.ids import PersonId


@runtime_checkable
class ActorIdentity(Protocol):
    """Resolves an authenticated person id into the actor the application works with."""

    async def resolve(self, person_id: PersonId) -> Actor | None:
        """Build the actor for this person.

        Roles are read **fresh on every request**, never cached in the session. A teacher
        removed from the staff at 10:00 must lose their write access at 10:00, not whenever
        their cookie happens to expire -- see ``docs/04-state-diagrams.md`` §7.

        Returns:
            The actor, or ``None`` if no such person exists. A valid session naming a deleted
            person resolves to ``None`` and is treated as unauthenticated, rather than as an
            actor with no roles: the two are different, and only one of them should be able
            to reach a page at all.
        """
        ...
