"""Academic history aggregate root: a student's durable grade transcript."""

from __future__ import annotations

from academy.domain.grades.grade import Grade
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.shared.entity import Entity
from academy.domain.shared.ids import PersonId, SectionId, SubjectId


class AcademicHistory(Entity[PersonId]):
    """The complete transcript of every grade a student has obtained over time.

    The history is identified by its student: there is exactly one per student. It is
    independent of course-section lifecycle -- grades stay here even after their section
    is deleted. The best (highest) grade per subject determines pass/fail.
    """

    def __init__(self, student_id: PersonId, entries: list[GradeEntry] | None = None) -> None:
        """Initialize an academic history.

        Args:
            student_id: The student this history belongs to (also its identity).
            entries: Initial grade entries.
        """
        self.id = student_id
        self._entries: list[GradeEntry] = list(entries or [])

    def record(self, entry: GradeEntry) -> None:
        """Append a grade entry to the transcript. Every attempt is kept."""
        self._entries.append(entry)

    def entries_for(self, subject_id: SubjectId) -> tuple[GradeEntry, ...]:
        """Return every grade entry recorded for ``subject_id``."""
        return tuple(entry for entry in self._entries if entry.subject_id == subject_id)

    def best_grade(self, subject_id: SubjectId) -> Grade | None:
        """Return the highest grade obtained for ``subject_id``, or ``None`` if untaken."""
        grades = [entry.grade for entry in self._entries if entry.subject_id == subject_id]
        return max(grades) if grades else None

    def has_passed(self, subject_id: SubjectId) -> bool:
        """Return whether the best grade for ``subject_id`` is a passing grade."""
        best = self.best_grade(subject_id)
        return best is not None and best.is_passing()

    def passed_subjects(self) -> frozenset[SubjectId]:
        """Return the ids of the subjects the student has passed."""
        return frozenset(entry.subject_id for entry in self._entries if self.has_passed(entry.subject_id))

    def detach_section(self, section_id: SectionId) -> None:
        """Remove the originating-section reference from entries of a deleted section."""
        self._entries = [
            entry.detached() if entry.source_section_id == section_id else entry for entry in self._entries
        ]

    @property
    def student_id(self) -> PersonId:
        """The student this history belongs to."""
        return self.id

    @property
    def entries(self) -> tuple[GradeEntry, ...]:
        """Every grade entry in the transcript (read-only view)."""
        return tuple(self._entries)
