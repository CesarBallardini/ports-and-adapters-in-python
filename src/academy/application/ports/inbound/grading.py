"""The driving port for everything a teacher does with grades.

Grouped by actor intent rather than one interface per use case (ADR-0003). The web router
that renders the grading screen depends on this and only this, so it cannot reach
``delete_section`` even by mistake -- interface segregation enforced by the type checker
rather than by reviewer attention.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from academy.application.commands import ListSectionGradesCommand, RecordGradeCommand
from academy.application.dtos import GradeRecordedDto, SectionGradesDto


@runtime_checkable
class ManageGrades(Protocol):
    """Recording and reading the grades of a course section."""

    async def record_grade(self, command: RecordGradeCommand) -> GradeRecordedDto:
        """Record one grade attempt (UC-22).

        The only write path to a grade in the system, and the only place ``Action.WRITE`` is
        ever granted. Every attempt is kept; the best of them determines the standing.

        Returns:
            The resulting standing, not merely an acknowledgement -- recording a 4 after an
            earlier 7 changes nothing about whether the subject is passed, and the answer
            should say so.

        Raises:
            AuthorizationError: If the actor does not teach a section this student is in.
            NotFoundError: If the section or the student does not exist.
            InvalidGradeError: If the grade is outside 0..10.
            StudentNotEnrolledError: If the student is not enrolled in this section.
        """
        ...

    async def list_section_grades(self, command: ListSectionGradesCommand) -> SectionGradesDto:
        """List a section's students with their standing (UC-21).

        Readable by the section's teacher and by an administrative employee -- who may read
        it but, per the spec, may never write a grade.

        Raises:
            AuthorizationError: If the actor holds no relation granting read on grades.
            NotFoundError: If the section does not exist.
        """
        ...
