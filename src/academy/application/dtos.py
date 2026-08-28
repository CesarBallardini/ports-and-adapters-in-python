"""What crosses the boundary outward.

Use cases return DTOs, never domain entities. The extra hop looks like ceremony until you
notice what it prevents:

* An adapter cannot invoke domain behaviour by accident. A :class:`SectionDto` has no
  ``enroll()`` to reach for, so business rules cannot migrate into a template or a router.
* The shape an HTTP client depends on stops being whatever shape the domain happens to have
  today, so the domain stays free to change.
* Jinja2 templates receive plain data. A template that could call a method would eventually
  call one, and rendering would start having side effects.

Everything here is frozen and built from primitives and other DTOs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Self

from academy.domain.academics.course_section import CourseSection
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.graduation.graduation import Graduation
from academy.domain.people.person import Person
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is making a request, as the application understands it.

    Deliberately carries no trace of *how* the actor was authenticated: a session cookie, a
    bearer token and a CLI invocation all produce the same value (ADR-0010). That is what
    makes the authentication mechanism provably irrelevant to authorization -- a use case
    could not branch on it even if it wanted to.
    """

    person_id: PersonId
    roles: frozenset[Role] = field(default_factory=frozenset)

    @property
    def is_administrator(self) -> bool:
        """Whether this actor holds the administrative role."""
        return Role.ADMINISTRATIVE_EMPLOYEE in self.roles

    def holds(self, role: Role) -> bool:
        """Return whether the actor holds ``role``."""
        return role in self.roles


@dataclass(frozen=True, slots=True)
class PersonDto:
    """A person, as shown to the outside."""

    id: str
    email: str
    full_name: str
    birth_date: date
    roles: tuple[str, ...]

    @classmethod
    def of(cls, person: Person) -> Self:
        """Project a :class:`~academy.domain.people.person.Person` into its view."""
        return cls(
            id=str(person.id),
            email=person.email.value,
            full_name=person.personal.full_name,
            birth_date=person.personal.birth_date,
            roles=tuple(sorted(role.value for role in person.roles)),
        )


@dataclass(frozen=True, slots=True)
class SectionDto:
    """A course section, with its roster size but not its roster."""

    id: str
    subject_id: str
    teacher_id: str
    term: str
    enrolled_count: int

    @classmethod
    def of(cls, section: CourseSection) -> Self:
        """Project a :class:`~academy.domain.academics.course_section.CourseSection`."""
        return cls(
            id=str(section.id),
            subject_id=str(section.subject_id),
            teacher_id=str(section.teacher_id),
            term=section.term.label(),
            enrolled_count=len(section.enrollments),
        )


@dataclass(frozen=True, slots=True)
class GradeEntryDto:
    """One recorded grade attempt."""

    subject_id: str
    term: str
    grade: int
    from_section_id: str | None


@dataclass(frozen=True, slots=True)
class SubjectStandingDto:
    """A student's standing in one subject: the best of their attempts.

    ``passed`` is computed from ``best_grade`` on every read and is never stored -- see
    ``docs/04-state-diagrams.md`` §5 for why that choice removes a whole class of staleness.
    """

    subject_id: str
    best_grade: int | None
    passed: bool
    attempts: int


@dataclass(frozen=True, slots=True)
class AcademicHistoryDto:
    """A student's full transcript, plus their standing per subject."""

    student_id: str
    entries: tuple[GradeEntryDto, ...]
    standings: tuple[SubjectStandingDto, ...]

    @classmethod
    def of(cls, history: AcademicHistory) -> Self:
        """Project an :class:`~academy.domain.grades.academic_history.AcademicHistory`."""
        subject_ids = {entry.subject_id for entry in history.entries}
        return cls(
            student_id=str(history.student_id),
            entries=tuple(
                GradeEntryDto(
                    subject_id=str(entry.subject_id),
                    term=entry.term.label(),
                    grade=entry.grade.value,
                    from_section_id=str(entry.source_section_id) if entry.source_section_id else None,
                )
                for entry in history.entries
            ),
            standings=tuple(
                sorted(
                    (
                        SubjectStandingDto(
                            subject_id=str(subject_id),
                            best_grade=best.value if (best := history.best_grade(subject_id)) else None,
                            passed=history.has_passed(subject_id),
                            attempts=len(history.entries_for(subject_id)),
                        )
                        for subject_id in subject_ids
                    ),
                    key=lambda standing: standing.subject_id,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class GradeRecordedDto:
    """The outcome of recording one grade.

    Returns the resulting *standing*, not just an acknowledgement, because that is the
    question the teacher actually has: recording a 4 after an earlier 7 changes nothing
    about whether the subject is passed, and the answer should say so.
    """

    student_id: str
    subject_id: str
    recorded_grade: int
    best_grade: int
    passed: bool


@dataclass(frozen=True, slots=True)
class GraduationDto:
    """A conferred graduation."""

    id: str
    student_id: str
    program_id: str
    credential_id: str
    conferred_on: date
    revoked: bool

    @classmethod
    def of(cls, graduation: Graduation) -> Self:
        """Project a :class:`~academy.domain.graduation.graduation.Graduation`."""
        return cls(
            id=str(graduation.id),
            student_id=str(graduation.student_id),
            program_id=str(graduation.program_id),
            credential_id=str(graduation.credential_id),
            conferred_on=graduation.conferred_on,
            revoked=not graduation.is_active(),
        )


@dataclass(frozen=True, slots=True)
class RowError:
    """One rejected row of an import, with enough detail to fix it.

    The line number is what makes partial success usable: a teacher gets back "rows 4, 17
    and 31 were rejected, and why", not one opaque failure for the whole file.
    """

    line: int
    reason: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportResultDto:
    """The report of one import run, successful or not.

    A run that rejected thirty of a hundred rows still *completed*: whether the outcome was
    acceptable is :meth:`ok`, a separate question from whether the run finished. Collapsing
    the two would make a partially-rejected import indistinguishable from an unreadable file.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: tuple[RowError, ...] = ()
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        """Whether every row was accepted."""
        return not self.errors

    @property
    def processed(self) -> int:
        """How many rows produced a change."""
        return self.created + self.updated


@dataclass(frozen=True, slots=True)
class SectionGradeRowDto:
    """One student's line on a course section's grade sheet."""

    student_id: str
    full_name: str
    best_grade: int | None
    passed: bool
    attempts: int


@dataclass(frozen=True, slots=True)
class SectionGradesDto:
    """A course section's roster, each student with their standing in its subject.

    Carries the student's name as well as their id, because the only two consumers -- an
    htmx table and an exported grade sheet -- both need it, and having the use case join it
    once is better than having each adapter fetch people separately and disagree about the
    order.
    """

    section_id: str
    subject_id: str
    term: str
    rows: tuple[SectionGradeRowDto, ...]
