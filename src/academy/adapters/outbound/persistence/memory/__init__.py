"""The in-memory persistence adapter.

A production-grade adapter that forgets everything when the process ends -- not a test double
(ADR-0014). It is what makes the unit and acceptance tiers fast, and it is only trustworthy
in that role because the same contract suite that runs against SQLAlchemy runs against it.
"""

from academy.adapters.outbound.persistence.memory.repositories import (
    MemoryAcademicHistoryRepository,
    MemoryConfigurationRepository,
    MemoryGuardianshipRepository,
    MemoryPersonRepository,
    MemorySectionRepository,
)
from academy.adapters.outbound.persistence.memory.store import (
    DEFAULT_AGE_OF_MAJORITY,
    MemoryStore,
    MemoryUnitOfWork,
)

__all__ = [
    'DEFAULT_AGE_OF_MAJORITY',
    'MemoryAcademicHistoryRepository',
    'MemoryConfigurationRepository',
    'MemoryGuardianshipRepository',
    'MemoryPersonRepository',
    'MemorySectionRepository',
    'MemoryStore',
    'MemoryUnitOfWork',
]
