"""Identifier generators: the random one, and the one that lets a test know what to expect.

Two implementations of the same port, and the pair is what makes an id-generating use case
assertable at all. A test that recorded a grade and then had to *discover* which id the job
got would be asserting the generator's behaviour by accident; with a sequential generator it
asserts the use case's.

Neither touches storage. The port promises identity before an entity is stored -- which is
what lets a use case build a complete aggregate, write its payload under a key derived from
its id, and save the whole thing once (ADR-0005).
"""

from __future__ import annotations

from itertools import count
from uuid import UUID, uuid4

from academy.application.jobs import JobId
from academy.domain.shared.ids import (
    CredentialId,
    GraduationId,
    GuardianshipId,
    PersonId,
    PlanId,
    ProgramId,
    SectionId,
    SubjectId,
)


class Uuid4IdGenerator:
    """Random identifiers, from the standard library.

    Satisfies :class:`~academy.application.ports.outbound.system.IdGenerator`. Version 4 and
    not version 7: these ids are handed to people in URLs, and a time-ordered id would tell a
    reader when a record was created and roughly how many were created before it.
    """

    def next_person_id(self) -> PersonId:
        """A fresh person identifier."""
        return PersonId(uuid4())

    def next_credential_id(self) -> CredentialId:
        """A fresh credential identifier."""
        return CredentialId(uuid4())

    def next_program_id(self) -> ProgramId:
        """A fresh degree-program identifier."""
        return ProgramId(uuid4())

    def next_plan_id(self) -> PlanId:
        """A fresh plan identifier."""
        return PlanId(uuid4())

    def next_subject_id(self) -> SubjectId:
        """A fresh subject identifier."""
        return SubjectId(uuid4())

    def next_section_id(self) -> SectionId:
        """A fresh course-section identifier."""
        return SectionId(uuid4())

    def next_graduation_id(self) -> GraduationId:
        """A fresh graduation identifier."""
        return GraduationId(uuid4())

    def next_guardianship_id(self) -> GuardianshipId:
        """A fresh guardianship identifier."""
        return GuardianshipId(uuid4())

    def next_job_id(self) -> JobId:
        """A fresh import-job identifier."""
        return JobId(uuid4())


class SequentialIdGenerator:
    """Identifiers counted up from one, per type.

    Satisfies :class:`~academy.application.ports.outbound.system.IdGenerator`. Not a test
    double in any way that matters -- it is the generator a reproducible fixture load or a
    deterministic demo dataset wants, as much as the one a test wants.

    Counters are **per type**, so the first person and the first subject are both number one.
    That is deliberate: a shared counter would make a test's expected ids depend on how many
    unrelated entities the fixture happened to create first, which is exactly the coupling
    this class exists to remove.
    """

    def __init__(self, start: int = 1) -> None:
        """Begin counting at ``start``.

        Args:
            start: The first number to hand out. Not zero by default, because ``UUID(int=0)``
                is the nil UUID and reads as "unset" to anyone who meets it in a log.
        """
        self._counters: dict[str, count[int]] = {}
        self._start = start

    def next_person_id(self) -> PersonId:
        """The next person identifier in sequence."""
        return PersonId(self._next('person'))

    def next_credential_id(self) -> CredentialId:
        """The next credential identifier in sequence."""
        return CredentialId(self._next('credential'))

    def next_program_id(self) -> ProgramId:
        """The next degree-program identifier in sequence."""
        return ProgramId(self._next('program'))

    def next_plan_id(self) -> PlanId:
        """The next plan identifier in sequence."""
        return PlanId(self._next('plan'))

    def next_subject_id(self) -> SubjectId:
        """The next subject identifier in sequence."""
        return SubjectId(self._next('subject'))

    def next_section_id(self) -> SectionId:
        """The next course-section identifier in sequence."""
        return SectionId(self._next('section'))

    def next_graduation_id(self) -> GraduationId:
        """The next graduation identifier in sequence."""
        return GraduationId(self._next('graduation'))

    def next_guardianship_id(self) -> GuardianshipId:
        """The next guardianship identifier in sequence."""
        return GuardianshipId(self._next('guardianship'))

    def next_job_id(self) -> JobId:
        """The next import-job identifier in sequence.

        A UUID like every other id, counted rather than random: ``UUID(int=1)`` is as readable
        in a log as ``job-1`` once you have seen one, and it keeps a job id the same shape as
        every other identifier in the system.
        """
        return JobId(self._next('job'))

    def _next(self, kind: str) -> UUID:
        """The next UUID for one kind, counting from ``start``."""
        return UUID(int=next(self._counter(kind)))

    def _counter(self, kind: str) -> count[int]:
        """The counter for one kind, created on first use."""
        return self._counters.setdefault(kind, count(self._start))
