"""Resolving relationships, and enforcing what they grant.

This module is one half of a deliberate split, and the split is the single design decision in
the codebase that would be most expensive to get wrong.

* **Deciding** what a relationship grants is a business rule -- a regulator could ask to see
  the grant matrix. It lives in the domain, as the pure
  :class:`~academy.domain.authorization.policy.AccessPolicy`, and does no I/O.
* **Discovering** which relationships hold requires reading repositories. It cannot live in
  the domain, so it lives here.

Collapsing the two would cost the grant matrix its testability: every authorization test
would need a database, and the rules a regulator cares about would be buried in query code.
Keeping them apart means :class:`~academy.domain.authorization.policy.AccessPolicy` is
exhaustively unit-tested with no infrastructure at all, and this module is tested for one
thing only -- whether it finds the relationships that really hold.

See ``docs/03-sequence-diagrams.md`` §2 for the call sequence and
``docs/06-class-diagram.md`` §5 for the structure.
"""

from __future__ import annotations

from academy.application.dtos import Actor
from academy.application.errors import AuthorizationError
from academy.application.ports.outbound.repositories import (
    ConfigurationRepository,
    GuardianshipRepository,
    PersonRepository,
    SectionRepository,
)
from academy.application.ports.outbound.system import Clock
from academy.domain.authorization.models import AccessDecision, AccessRequest, Action, Relation, ResourceType
from academy.domain.authorization.policy import AccessPolicy
from academy.domain.shared.ids import PersonId


class RelationshipResolver:
    """Finds which relationships connect an actor to the owner of a record.

    Self-served, in the sense of ADR-0003 and academy's A-05: the answer comes from our own
    repositories, not from an external authorization service. There is no relationship store
    to keep in sync -- teaching is derived from sections, guardianship from guardianships and
    the ward's age -- so a relationship cannot go stale relative to the data it describes.
    """

    def __init__(
        self,
        sections: SectionRepository,
        guardianships: GuardianshipRepository,
        people: PersonRepository,
        configuration: ConfigurationRepository,
        clock: Clock,
    ) -> None:
        """Wire the resolver to the repositories the relationships are derived from.

        Args:
            sections: Source of the *teacher-of-section* relation.
            guardianships: Source of the stored guardian-to-ward links.
            people: Needed to read the ward's age when deciding whether a link still applies.
            configuration: Source of the global age of majority.
            clock: Supplies ``today`` to the domain, which never reads it itself.
        """
        self._sections = sections
        self._guardianships = guardianships
        self._people = people
        self._configuration = configuration
        self._clock = clock

    async def relations_of(self, actor: Actor, owner_id: PersonId) -> frozenset[Relation]:
        """Every relation that currently connects ``actor`` to ``owner_id``.

        Relations accumulate rather than exclude one another. A teacher who is also the
        guardian of one of their own students holds both, and the policy grants the union --
        which is the behaviour the spec describes when it points out that one human may be a
        mother, a teacher and a student at once.

        Args:
            actor: The actor making the request.
            owner_id: The person whose records are being touched.

        Returns:
            The relations that hold right now. Possibly empty, which the policy will read as
            a denial.
        """
        relations: set[Relation] = set()

        if actor.person_id == owner_id:
            relations.add(Relation.SELF)

        if actor.is_administrator:
            relations.add(Relation.ADMINISTRATOR)

        if await self._teaches(actor.person_id, owner_id):
            relations.add(Relation.TEACHER_OF_SECTION)

        if await self._is_guardian_of(actor.person_id, owner_id):
            relations.add(Relation.GUARDIAN_OF)

        return frozenset(relations)

    async def _teaches(self, teacher_id: PersonId, student_id: PersonId) -> bool:
        """Whether ``teacher_id`` teaches a section ``student_id`` is enrolled in."""
        return student_id in await self._sections.teaching_students_of(teacher_id)

    async def _is_guardian_of(self, guardian_id: PersonId, ward_id: PersonId) -> bool:
        """Whether a stored guardianship between these two currently applies.

        "Currently" is the whole difficulty. The link is stored, but whether it *applies* is
        computed from the ward's age against the global age of majority, on every check --
        so the relation disappears on the ward's birthday with nothing having been written
        and no job having run (``docs/04-state-diagrams.md`` §6).

        The rule itself is the domain's (``Guardianship.applies``); this method's only job is
        to fetch the three facts it needs and hand them over.
        """
        links = [link for link in await self._guardianships.wards_of(guardian_id) if link.ward_id == ward_id]
        if not links:
            return False

        ward = await self._people.get(ward_id)
        if ward is None:
            # The link outlived the person. Not an error to answer here -- whoever is asking
            # gets "no relation", and the dangling record is a data problem for elsewhere.
            return False

        age_of_majority = await self._configuration.age_of_majority()
        today = self._clock.today()
        return any(link.applies(ward, age_of_majority, today) for link in links)


class AccessGuard:
    """Resolve, decide, and raise -- so no use case ever repeats the dance.

    A *Pure Fabrication* in GRASP terms: it models nothing in the business, and exists so
    that the two-step nature of an authorization check does not have to appear in twenty use
    cases, where the twenty-first would eventually forget the second step.
    """

    def __init__(self, resolver: RelationshipResolver, policy: AccessPolicy | None = None) -> None:
        """Wire the guard to its resolver and to the domain policy.

        Args:
            resolver: Discovers which relations hold.
            policy: Decides what they grant. Defaults to the standard
                :class:`~academy.domain.authorization.policy.AccessPolicy`, since the grant
                matrix is a domain rule rather than a deployment choice -- it is injectable
                only so tests can substitute a policy, never so a deployment can.
        """
        self._resolver = resolver
        self._policy = policy or AccessPolicy()

    async def require(
        self,
        actor: Actor,
        action: Action,
        resource: ResourceType,
        owner_id: PersonId,
    ) -> None:
        """Allow the operation, or raise.

        Args:
            actor: Who is asking.
            action: Read or write.
            resource: Grades, or academic history.
            owner_id: The person whose records are being touched.

        Raises:
            AuthorizationError: If no resolved relation grants ``action`` on ``resource``.
                The policy's own reason is carried through, so a denial can be explained in
                a log without re-deriving why.
        """
        decision = await self.decide(actor, action, resource, owner_id)
        if not decision.allowed:
            raise AuthorizationError(decision.reason)

    async def decide(
        self,
        actor: Actor,
        action: Action,
        resource: ResourceType,
        owner_id: PersonId,
    ) -> AccessDecision:
        """Return the decision without raising.

        Used where a denial is not exceptional: a listing that shows a guardian only the
        wards they may see, for instance, filters rather than fails.
        """
        relations = await self._resolver.relations_of(actor, owner_id)
        request = AccessRequest(
            actor_id=actor.person_id,
            action=action,
            resource=resource,
            owner_id=owner_id,
            actor_roles=actor.roles,
            relations=relations,
        )
        return self._policy.decide(request)
