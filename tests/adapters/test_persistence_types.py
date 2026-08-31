"""Unit tests for the column types that carry value objects into SQL and back.

`unit` tier, and no database: a ``TypeDecorator`` is a pair of pure functions, and testing it
directly is both faster and more precise than inferring its behaviour from a round trip through
a schema.

Two properties matter here and neither is obvious from a happy-path round trip:

* the **domain validates on the way out** -- a value the database should never have held raises
  where it is read, naming the value, rather than surviving to confuse something downstream;
* the stored form is **portable and stable** -- the same bytes on SQLite and PostgreSQL, and the
  same bytes for an unchanged aggregate, so a diff of two dumps shows only real changes.
"""

from uuid import UUID

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.types import TypeDecorator

from academy.adapters.outbound.persistence.sqlalchemy.types import (
    AgeOfMajorityColumn,
    CredentialIdsColumn,
    EmailColumn,
    EnrollmentsColumn,
    GradeEntriesColumn,
    IdColumn,
    ImportResultColumn,
    RolesColumn,
    TermColumn,
)
from academy.application.dtos import ImportResultDto, RowError
from academy.domain.academics.course_section import Enrollment
from academy.domain.academics.term import InvalidTermError, Term
from academy.domain.grades.grade import Grade, InvalidGradeError
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.people.age_of_majority import AgeOfMajority, InvalidAgeOfMajorityError
from academy.domain.people.email import Email, InvalidEmailError
from academy.domain.people.role import Role
from academy.domain.shared.ids import CredentialId, PersonId, SectionId, SubjectId

ADA = PersonId(UUID(int=1))
BOB = PersonId(UUID(int=2))
MATH = SubjectId(UUID(int=20))
SECTION = SectionId(UUID(int=10))
CREDENTIAL = CredentialId(UUID(int=30))

# A real dialect, though none of these types consults it: every one of them stores the same
# bytes everywhere, which is the property ADR-0007 depends on. Passing the SQLite dialect rather
# than None keeps the calls honest -- these are the arguments SQLAlchemy itself passes.
DIALECT = sqlite.dialect()


def _round_trip[T](column: TypeDecorator[T], value: T | None) -> T | None:
    """Bind a value the way SQLAlchemy would, then read it back."""
    return column.process_result_value(column.process_bind_param(value, DIALECT), DIALECT)


@pytest.mark.unit
def test_an_identifier_comes_back_as_its_own_type() -> None:
    column = IdColumn(PersonId)

    assert column.process_bind_param(ADA, DIALECT) == '00000000-0000-0000-0000-000000000001'
    assert _round_trip(column, ADA) == ADA


@pytest.mark.unit
def test_an_identifier_column_rebuilds_the_type_it_was_given() -> None:
    # The reason the class is passed in: a SectionId must not come out of a column that holds
    # people, and the two are indistinguishable as strings.
    assert isinstance(_round_trip(IdColumn(SectionId), SECTION), SectionId)
    assert isinstance(_round_trip(IdColumn(PersonId), ADA), PersonId)


@pytest.mark.unit
def test_an_email_is_normalised_on_the_way_out_as_well_as_in() -> None:
    assert _round_trip(EmailColumn(), Email('Ada@Academy.TEST')) == Email('ada@academy.test')


@pytest.mark.unit
def test_an_email_the_database_should_never_have_held_raises_where_it_is_read() -> None:
    with pytest.raises(InvalidEmailError):
        EmailColumn().process_result_value('not-an-address', DIALECT)


@pytest.mark.unit
def test_a_term_is_stored_as_its_own_label() -> None:
    assert TermColumn().process_bind_param(Term(2026, 1), DIALECT) == '2026-T1'
    assert _round_trip(TermColumn(), Term(2026, 2)) == Term(2026, 2)


@pytest.mark.unit
def test_a_term_outside_the_domain_raises_where_it_is_read() -> None:
    with pytest.raises(InvalidTermError):
        TermColumn().process_result_value('2026-T7', DIALECT)


@pytest.mark.unit
def test_an_age_of_majority_round_trips_and_is_revalidated() -> None:
    assert _round_trip(AgeOfMajorityColumn(), AgeOfMajority(18)) == AgeOfMajority(18)

    with pytest.raises(InvalidAgeOfMajorityError):
        AgeOfMajorityColumn().process_result_value('0', DIALECT)


@pytest.mark.unit
def test_roles_are_stored_in_a_stable_order() -> None:
    # An unchanged person must produce unchanged bytes, or every dump diff is noise and every
    # write looks like a change to whatever watches the table.
    column = RolesColumn()
    one = column.process_bind_param({Role.TEACHER, Role.STUDENT}, DIALECT)
    other = column.process_bind_param({Role.STUDENT, Role.TEACHER}, DIALECT)

    assert one == other
    assert _round_trip(column, {Role.TEACHER, Role.STUDENT}) == {Role.TEACHER, Role.STUDENT}


@pytest.mark.unit
def test_held_credentials_round_trip_as_their_own_id_type() -> None:
    restored = _round_trip(CredentialIdsColumn(), {CREDENTIAL})

    assert restored is not None
    assert restored == {CREDENTIAL}
    assert all(isinstance(item, CredentialId) for item in restored)


@pytest.mark.unit
def test_enrollments_keep_their_order() -> None:
    enrollments = [Enrollment(student_id=BOB), Enrollment(student_id=ADA)]

    assert _round_trip(EnrollmentsColumn(), enrollments) == enrollments


@pytest.mark.unit
def test_grade_entries_keep_every_attempt_in_order() -> None:
    # A transcript is a record of attempts in the order they happened, and `best_grade` is
    # computed over all of them -- so losing one, or reordering them, changes an outcome.
    entries = [
        GradeEntry(subject_id=MATH, term=Term(2026, 1), grade=Grade(4), source_section_id=SECTION),
        GradeEntry(subject_id=MATH, term=Term(2026, 2), grade=Grade(8), source_section_id=None),
    ]

    assert _round_trip(GradeEntriesColumn(), entries) == entries


@pytest.mark.unit
def test_a_detached_entry_keeps_its_grade_and_loses_only_its_section() -> None:
    # What a deleted section leaves behind: the grade survives in the transcript regardless.
    entry = GradeEntry(subject_id=MATH, term=Term(2026, 1), grade=Grade(9)).detached()

    restored = _round_trip(GradeEntriesColumn(), [entry])

    assert restored is not None
    assert restored[0].source_section_id is None
    assert restored[0].grade == Grade(9)


@pytest.mark.unit
def test_a_grade_outside_the_scale_raises_where_it_is_read() -> None:
    corrupt = f'[{{"subject_id": "{MATH}", "term": "2026-T1", "grade": 11, "source_section_id": null}}]'

    with pytest.raises(InvalidGradeError):
        GradeEntriesColumn().process_result_value(corrupt, DIALECT)


@pytest.mark.unit
def test_a_missing_collection_reads_as_an_empty_one() -> None:
    # Both directions: nothing stored, and nothing given. A column that answered None would
    # make every aggregate that holds a collection check for it.
    assert RolesColumn().process_result_value(None, DIALECT) == set()
    assert CredentialIdsColumn().process_result_value(None, DIALECT) == set()
    assert EnrollmentsColumn().process_result_value(None, DIALECT) == []
    assert GradeEntriesColumn().process_result_value(None, DIALECT) == []
    assert RolesColumn().process_bind_param(None, DIALECT) == '[]'


@pytest.mark.unit
def test_an_import_report_round_trips_with_its_rejected_rows() -> None:
    report = ImportResultDto(
        created=2,
        skipped=1,
        dry_run=True,
        errors=(RowError(line=3, reason='no student with that email', values=('x@y.test', '8')),),
    )

    assert _round_trip(ImportResultColumn(), report) == report


@pytest.mark.unit
def test_a_job_with_no_report_stores_nothing() -> None:
    # A job that has not finished has no report, and that is different from one whose report
    # was empty.
    assert ImportResultColumn().process_bind_param(None, DIALECT) is None
    assert ImportResultColumn().process_result_value(None, DIALECT) is None
