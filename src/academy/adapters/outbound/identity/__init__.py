"""Adapters for :class:`~academy.application.ports.outbound.identity.ActorIdentity`.

Two of them, and the pair is what makes the port an abstraction rather than a name for one class
(ADR-0014). They are not a production one and a fake: each answers a case the other genuinely
cannot. :class:`RepositoryActorIdentity` needs a person record and is what a running system uses;
:class:`StaticActorIdentity` needs no storage at all and is what an empty one uses to create its
first person. A single contract suite holds both to the port's docstring.
"""

from academy.adapters.outbound.identity.repository import RepositoryActorIdentity
from academy.adapters.outbound.identity.static import StaticActorIdentity

__all__ = ['RepositoryActorIdentity', 'StaticActorIdentity']
