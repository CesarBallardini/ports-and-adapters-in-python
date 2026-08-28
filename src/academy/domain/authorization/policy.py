"""Self-served, relationship-based access policy (the domain's decision point)."""

from __future__ import annotations

from academy.domain.authorization.models import (
    AccessDecision,
    AccessRequest,
    Action,
    Permission,
    Relation,
    ResourceType,
)

# The grant matrix: for each relation, the permissions it grants.
_GRANTS: dict[Relation, frozenset[Permission]] = {
    Relation.SELF: frozenset(
        {
            Permission(ResourceType.GRADES, Action.READ),
            Permission(ResourceType.ACADEMIC_HISTORY, Action.READ),
        }
    ),
    Relation.TEACHER_OF_SECTION: frozenset(
        {
            Permission(ResourceType.GRADES, Action.READ),
            Permission(ResourceType.GRADES, Action.WRITE),
        }
    ),
    Relation.GUARDIAN_OF: frozenset(
        {
            Permission(ResourceType.GRADES, Action.READ),
            Permission(ResourceType.ACADEMIC_HISTORY, Action.READ),
        }
    ),
    Relation.ADMINISTRATOR: frozenset(
        {
            Permission(ResourceType.GRADES, Action.READ),
            Permission(ResourceType.ACADEMIC_HISTORY, Action.READ),
        }
    ),
}


class AccessPolicy:
    """Pure authorization policy: decides allow/deny from a resolved access request.

    The policy is a pure function of its inputs (the actor's resolved relations plus the
    requested resource and action). Resolving which relations hold is the application's
    job; this class contains no I/O and never reads a repository.
    """

    def decide(self, request: AccessRequest) -> AccessDecision:
        """Decide whether ``request`` is allowed under the grant matrix."""
        wanted = request.permission
        for relation in request.relations:
            if wanted in _GRANTS.get(relation, frozenset()):
                return AccessDecision.allow(f'granted by {relation.value}')
        return AccessDecision.deny('no relation grants this action on this resource')
