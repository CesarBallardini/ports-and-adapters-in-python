"""Base class for identity-based domain entities."""

from __future__ import annotations


class Entity[IdT]:
    """Base for entities: equality and hashing are based on identity, not attributes.

    Two entities are equal when they are of the same concrete type and share the same
    ``id``. Subclasses parameterize ``IdT`` with their identifier type and assign ``id``
    in their initializer.
    """

    id: IdT

    def __eq__(self, other: object) -> bool:
        """Return whether ``other`` is the same entity (same concrete type and id)."""
        return isinstance(other, Entity) and type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        """Hash by concrete type and id, so entities work as set members and dict keys."""
        return hash((type(self), self.id))
