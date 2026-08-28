"""Authorization request/decision value objects and enumerations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId


class Action(Enum):
    """An action a subject may perform on a resource."""

    READ = 'read'
    WRITE = 'write'


class ResourceType(Enum):
    """A protected resource type."""

    GRADES = 'grades'
    ACADEMIC_HISTORY = 'academic_history'


class Relation(Enum):
    """A relationship between the actor and the record's owner that can grant access."""

    SELF = 'self'
    TEACHER_OF_SECTION = 'teacher_of_section'
    GUARDIAN_OF = 'guardian_of'
    ADMINISTRATOR = 'administrator'


class Permission(NamedTuple):
    """A (resource, action) pair: the unit a relation may grant."""

    resource: ResourceType
    action: Action


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """A resolved request to act on a resource owned by some person.

    ``relations`` holds the relationships that have already been resolved (self-served)
    between the actor and the record owner; the policy decides purely from these inputs.
    """

    actor_id: PersonId
    action: Action
    resource: ResourceType
    owner_id: PersonId
    actor_roles: frozenset[Role] = field(default_factory=frozenset)
    relations: frozenset[Relation] = field(default_factory=frozenset)

    @property
    def permission(self) -> Permission:
        """The (resource, action) permission this request asks for."""
        return Permission(self.resource, self.action)


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """The outcome of an authorization check."""

    allowed: bool
    reason: str

    @classmethod
    def allow(cls, reason: str) -> AccessDecision:
        """Build an allowing decision."""
        return cls(allowed=True, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> AccessDecision:
        """Build a denying decision."""
        return cls(allowed=False, reason=reason)
