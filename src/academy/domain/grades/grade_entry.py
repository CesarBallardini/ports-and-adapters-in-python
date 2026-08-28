"""Grade entry value object recorded in a student's academic history."""

from __future__ import annotations

from dataclasses import dataclass, replace

from academy.domain.academics.term import Term
from academy.domain.grades.grade import Grade
from academy.domain.shared.ids import SectionId, SubjectId


@dataclass(frozen=True, slots=True)
class GradeEntry:
    """A single grade obtained for a subject in a term.

    ``source_section_id`` records the course section the grade came from, or ``None`` once
    that section has been deleted (the grade survives in the transcript regardless).
    """

    subject_id: SubjectId
    term: Term
    grade: Grade
    source_section_id: SectionId | None = None

    def detached(self) -> GradeEntry:
        """Return a copy with the originating section reference removed."""
        return replace(self, source_section_id=None)
