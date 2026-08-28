"""Unit tests for the grades bounded context."""

from uuid import UUID

import pytest

from academy.domain.academics.term import Term
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.grades.grade import Grade, InvalidGradeError
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

SUBJECT = SubjectId(UUID(int=1))
OTHER_SUBJECT = SubjectId(UUID(int=2))
TERM = Term(2026, 1)


def entry(value: int, subject: SubjectId = SUBJECT, section: int | None = None) -> GradeEntry:
    source = SectionId(UUID(int=section)) if section is not None else None
    return GradeEntry(subject, TERM, Grade(value), source)


@pytest.mark.unit
@pytest.mark.parametrize('bad', [-1, 11, 100])
def test_grade_out_of_range_raises(bad: int) -> None:
    with pytest.raises(InvalidGradeError):
        Grade(bad)


@pytest.mark.unit
def test_passing_threshold() -> None:
    assert not Grade(5).is_passing()
    assert Grade(6).is_passing()


@pytest.mark.unit
def test_grades_are_ordered_by_value() -> None:
    assert max([Grade(4), Grade(9), Grade(7)]) == Grade(9)


@pytest.mark.unit
def test_grade_entry_detached_drops_section() -> None:
    assert entry(7, section=5).detached().source_section_id is None


@pytest.mark.unit
def test_best_grade_returns_highest_attempt() -> None:
    history = AcademicHistory(PersonId(UUID(int=1)))
    history.record(entry(4))
    history.record(entry(8))
    history.record(entry(6))

    assert history.best_grade(SUBJECT) == Grade(8)


@pytest.mark.unit
def test_best_grade_is_none_when_subject_untaken() -> None:
    history = AcademicHistory(PersonId(UUID(int=1)))

    assert history.best_grade(SUBJECT) is None


@pytest.mark.unit
def test_has_passed_uses_best_grade() -> None:
    history = AcademicHistory(PersonId(UUID(int=1)))
    history.record(entry(3))
    history.record(entry(6))

    assert history.has_passed(SUBJECT)


@pytest.mark.unit
def test_passed_subjects() -> None:
    history = AcademicHistory(PersonId(UUID(int=1)))
    history.record(entry(9, subject=SUBJECT))
    history.record(entry(2, subject=OTHER_SUBJECT))

    assert history.passed_subjects() == {SUBJECT}


@pytest.mark.unit
def test_detach_section_keeps_grades_but_drops_reference() -> None:
    history = AcademicHistory(PersonId(UUID(int=1)))
    history.record(entry(7, section=5))

    history.detach_section(SectionId(UUID(int=5)))

    assert history.best_grade(SUBJECT) == Grade(7)
    assert history.entries[0].source_section_id is None
