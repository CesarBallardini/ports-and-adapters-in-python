"""Unit tests for the self-served access policy."""

from uuid import UUID

import pytest

from academy.domain.authorization.models import (
    AccessRequest,
    Action,
    Relation,
    ResourceType,
)
from academy.domain.authorization.policy import AccessPolicy
from academy.domain.shared.ids import PersonId

ACTOR = PersonId(UUID(int=1))
OWNER = PersonId(UUID(int=2))


def request(
    action: Action,
    resource: ResourceType,
    relations: set[Relation],
) -> AccessRequest:
    return AccessRequest(ACTOR, action, resource, OWNER, relations=frozenset(relations))


@pytest.mark.unit
@pytest.mark.parametrize(
    'action,resource,relations,expected',
    [
        (Action.READ, ResourceType.GRADES, {Relation.SELF}, True),
        (Action.WRITE, ResourceType.GRADES, {Relation.SELF}, False),
        (Action.READ, ResourceType.ACADEMIC_HISTORY, {Relation.SELF}, True),
        (Action.WRITE, ResourceType.GRADES, {Relation.TEACHER_OF_SECTION}, True),
        (Action.READ, ResourceType.ACADEMIC_HISTORY, {Relation.TEACHER_OF_SECTION}, False),
        (Action.READ, ResourceType.GRADES, {Relation.GUARDIAN_OF}, True),
        (Action.WRITE, ResourceType.GRADES, {Relation.GUARDIAN_OF}, False),
        (Action.READ, ResourceType.ACADEMIC_HISTORY, {Relation.ADMINISTRATOR}, True),
        (Action.WRITE, ResourceType.GRADES, {Relation.ADMINISTRATOR}, False),
        (Action.WRITE, ResourceType.ACADEMIC_HISTORY, {Relation.TEACHER_OF_SECTION}, False),
        (Action.READ, ResourceType.GRADES, set(), False),
    ],
)
def test_grant_matrix(
    action: Action,
    resource: ResourceType,
    relations: set[Relation],
    expected: bool,
) -> None:
    decision = AccessPolicy().decide(request(action, resource, relations))

    assert decision.allowed is expected


@pytest.mark.unit
def test_any_granting_relation_allows() -> None:
    decision = AccessPolicy().decide(
        request(Action.WRITE, ResourceType.GRADES, {Relation.SELF, Relation.TEACHER_OF_SECTION})
    )

    assert decision.allowed
