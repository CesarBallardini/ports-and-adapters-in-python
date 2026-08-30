"""Storage ports, one per aggregate, expressed in domain terms.

**All async** (ADR-0005): persistence is I/O.

Every protocol here is a *specification*, not a description. The contract test suite asserts
exactly what these docstrings claim, against the in-memory adapter and the SQLAlchemy adapter
in the same run (ADR-0014) -- so "returns ``None`` when absent" and "raises ``NotFoundError``"
are statements the build checks, not comments.

Two conventions hold throughout, and the asymmetry between them is deliberate:

* a **lookup** that finds nothing returns ``None`` -- absence is a normal outcome of a search;
* an **update or delete** of something absent raises :class:`NotFoundError` -- absence is a
  broken expectation, and a caller that meant to update a record has already assumed it exists.

Query methods are named after the questions the use cases actually ask. There is no
``find_by(**criteria)``: such a method lets a use case push its logic down into the adapter,
where a second adapter would implement it differently, and the port silently stops being an
abstraction.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from academy.application.enrollments import PlanEnrollment

# Imports are top-level throughout, per the project convention -- see CLAUDE.md. Here it is
# not merely a convention: the names below are subscripted in class bases
# (``Repository[Person, PersonId]``), and a class base is an ordinary runtime expression
# rather than an annotation, so `from __future__ import annotations` does not defer it and
# a deferred import would be a NameError on import. Depending on the domain from here is
# exactly right; ADR-0004 checks it is only ever that way round.
from academy.application.jobs import ImportJob, JobId, JobStatus
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.degree_program import DegreeProgram
from academy.domain.academics.subject import Subject
from academy.domain.academics.term import Term
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.graduation.graduation import Graduation
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.credential import Credential
from academy.domain.people.person import Person
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


@runtime_checkable
class Repository[EntityT, IdT](Protocol):
    """The storage operations every aggregate needs.

    Generic over the aggregate and its identifier type, so a ``PersonRepository`` cannot be
    passed where a ``SectionRepository`` is expected even though their shapes match.

    Both parameters carry the ``T`` suffix, and neither is decoration: ``Entity`` and ``Id``
    are already taken by real classes -- :class:`~academy.domain.shared.entity.Entity`, which
    the aggregates inherit, and the ``_Id`` base of every identifier in
    :mod:`~academy.domain.shared.ids` -- so an unsuffixed parameter would read as the class it
    stands in for. ``IdT`` is the domain's own spelling, fixed there by ADR-0002; ``EntityT``
    is named to match it rather than left as a bare letter.
    """

    async def get(self, entity_id: IdT) -> EntityT | None:
        """Fetch one aggregate by identity.

        Returns:
            The aggregate, or ``None`` if no such record exists.
        """
        ...

    async def add(self, entity: EntityT) -> None:
        """Store an aggregate that is not yet stored.

        Raises:
            ConflictError: If an aggregate with the same identity, or violating a uniqueness
                constraint, already exists.
        """
        ...

    async def save(self, entity: EntityT) -> None:
        """Persist changes to an already-stored aggregate.

        Raises:
            NotFoundError: If no aggregate with that identity is stored.
            ConflictError: If the change violates a uniqueness constraint.
        """
        ...

    async def delete(self, entity_id: IdT) -> None:
        """Remove an aggregate.

        Raises:
            NotFoundError: If no aggregate with that identity is stored.
        """
        ...

    async def list_all(self) -> list[EntityT]:
        """Every stored aggregate.

        Returns:
            A list in a **stable, total order** -- by natural key, then by id to break ties.
            Adapters must not return insertion order or whatever the database volunteers:
            callers paginate and diff these results, and two adapters that ordered
            differently would fail the contract suite here rather than in production.
        """
        ...


@runtime_checkable
class PersonRepository(Repository[Person, PersonId], Protocol):
    """People, with their roles and held credentials."""

    async def by_email(self, email: str) -> Person | None:
        """Fetch the person with this email address.

        Email is the login identifier and is unique across people. The argument is a plain
        string rather than an ``Email``: callers include an authentication adapter holding
        whatever a user typed, and rejecting a malformed address is the value object's job
        at construction, not this lookup's.

        Returns:
            The person, or ``None`` if no person has that address. Matching is
            case-insensitive, because ``Email`` normalises to lower case on construction.
        """
        ...

    async def by_ids(self, person_ids: list[PersonId]) -> list[Person]:
        """Fetch several people at once.

        Exists so bulk imports and roster listings do not issue one query per row. Missing
        ids are **omitted** rather than raising: the caller is asking who exists, and a bulk
        import must report an unknown student as a rejected row, not abandon the file.

        Returns:
            The people that were found, in the same order as ``person_ids``.
        """
        ...

    async def holders_of(self, credential_id: CredentialId) -> list[Person]:
        """Every person holding this credential."""
        ...


@runtime_checkable
class ProgramRepository(Repository[DegreeProgram, ProgramId], Protocol):
    """Degree programs, each owning its plans.

    Plans have no repository of their own: they are part of the ``DegreeProgram`` aggregate,
    and the "exactly one active plan" invariant can only be enforced by loading the whole
    program. A ``PlanRepository`` would let a caller activate a plan without the program
    present, and the invariant would have nowhere left to live.
    """

    async def containing_plan(self, plan_id: PlanId) -> DegreeProgram | None:
        """Fetch the program that owns this plan.

        Returns:
            The owning program, or ``None`` if no program contains that plan.
        """
        ...

    async def with_active_plan(self) -> list[DegreeProgram]:
        """Every program that currently has an active plan."""
        ...


@runtime_checkable
class SubjectRepository(Repository[Subject, SubjectId], Protocol):
    """Subjects, addressable independently of the plans that include them."""

    async def by_name(self, name: str) -> Subject | None:
        """Fetch a subject by its exact name.

        Used by the bulk subject import, which identifies subjects the way a spreadsheet
        does -- by name -- rather than by an id a registrar would never type.
        """
        ...

    async def by_ids(self, subject_ids: list[SubjectId]) -> list[Subject]:
        """Fetch several subjects at once, omitting any that do not exist."""
        ...


@runtime_checkable
class CredentialRepository(Repository[Credential, CredentialId], Protocol):
    """Credentials and the subjects they qualify their holder to teach."""

    async def qualifying_for(self, subject_id: SubjectId) -> list[Credential]:
        """Every credential that qualifies its holder to teach this subject.

        This is the query behind the hard-enforced teacher qualification of UC-14: a section
        cannot be created unless the teacher holds one of these.
        """
        ...

    async def held_by(self, person_id: PersonId) -> list[Credential]:
        """Every credential held by this person.

        ``Person`` stores only credential *ids*, so resolving them to credentials is
        storage's job. That is exactly the split that keeps the domain free of lazy loading.
        """
        ...


@runtime_checkable
class SectionRepository(Repository[CourseSection, SectionId], Protocol):
    """Course sections, including their enrollments.

    Enrollments have no repository: they are values inside the ``CourseSection`` aggregate,
    so enrolling a student is a change to the section and is saved as one.
    """

    async def for_teacher(self, teacher_id: PersonId) -> list[CourseSection]:
        """Every section this person teaches, most recent term first."""
        ...

    async def for_student(self, student_id: PersonId) -> list[CourseSection]:
        """Every section this person is enrolled in, most recent term first."""
        ...

    async def in_term(self, term: Term) -> list[CourseSection]:
        """Every section running in this term."""
        ...

    async def subjects_enrolled_by(self, student_id: PersonId) -> frozenset[SubjectId]:
        """The subjects this student already has a section for.

        A dedicated query rather than a list-and-project in the use case, because it is the
        precondition of the "not already enrolled in this subject" rule (UC-18) and a student
        with a long history should not have every section loaded to answer it.
        """
        ...

    async def teaching_students_of(self, teacher_id: PersonId) -> frozenset[PersonId]:
        """Every student enrolled in any section this person teaches.

        The query behind the *teacher-of-section* relation in the ``RelationshipResolver``.
        It is asked on essentially every grade request, which is why it exists as one
        question rather than a loop over sections in the application layer.
        """
        ...


@runtime_checkable
class AcademicHistoryRepository(Repository[AcademicHistory, PersonId], Protocol):
    """Student transcripts, keyed by student.

    There is exactly one history per student, and it is identified by the student's own id
    rather than by a separate key.
    """

    async def get_or_create(self, student_id: PersonId) -> AcademicHistory:
        """Fetch this student's transcript, creating an empty one if they have none.

        A student's first grade would otherwise force every caller to write the same
        find-or-create dance, and to get the "empty transcript" and "no such student"
        cases confused. This method never raises for a student with no grades yet; whether
        the *person* exists is a different question, asked of ``PersonRepository``.

        A created history is **stored**, not merely returned, so that the caller's
        subsequent :meth:`Repository.save` finds it rather than raising ``NotFoundError``.
        Recording a student's first grade is exactly that sequence, and an adapter that
        returned an unstored object would break on it while satisfying every other
        assertion here.
        """
        ...

    async def for_students(self, student_ids: list[PersonId]) -> list[AcademicHistory]:
        """Fetch several transcripts at once, omitting students who have none.

        Used by graduation candidate listing, which examines a whole cohort.
        """
        ...


@runtime_checkable
class GraduationRepository(Repository[Graduation, GraduationId], Protocol):
    """Conferred graduations, including revoked ones.

    Revoked records are never deleted: revocation is a state, and the audit trail is the
    reason the conferral was stored rather than computed in the first place.
    """

    async def for_student(self, student_id: PersonId) -> list[Graduation]:
        """Every graduation record for this student, active or revoked, newest first."""
        ...

    async def active(self) -> list[Graduation]:
        """Every graduation currently in the active state.

        The input to the scheduled reconciliation of UC-35.
        """
        ...


@runtime_checkable
class GuardianshipRepository(Repository[Guardianship, GuardianshipId], Protocol):
    """Stored guardian-to-ward links.

    Storage answers only *who is linked to whom*. Whether a link currently **applies** is a
    domain rule evaluated against the ward's age and the global age of majority, and it is
    computed on read (``docs/04-state-diagrams.md`` §6). No adapter may filter these results
    by age: doing so would move a domain rule into storage and make the transition that
    happens on a birthday invisible to the test suite.
    """

    async def wards_of(self, guardian_id: PersonId) -> list[Guardianship]:
        """Every stored guardianship where this person is the guardian."""
        ...

    async def guardians_of(self, ward_id: PersonId) -> list[Guardianship]:
        """Every stored guardianship where this person is the ward."""
        ...


@runtime_checkable
class PlanEnrollmentRepository(Protocol):
    """Which plan each student is enrolled in.

    An application-owned record rather than a domain aggregate -- see
    :mod:`academy.application.enrollments` for why the domain does not model this.
    """

    async def for_student(self, student_id: PersonId) -> PlanEnrollment | None:
        """This student's plan enrollment, or ``None`` if they have none."""
        ...

    async def add(self, enrollment: PlanEnrollment) -> None:
        """Record a student's enrollment in a plan.

        Raises:
            ConflictError: If the student is already enrolled in a plan. A student is
                enrolled in exactly one (spec §3), so re-enrolling is a mistake worth
                reporting rather than an update worth performing.
        """
        ...

    async def for_plan(self, plan_id: PlanId) -> list[PlanEnrollment]:
        """Every student enrolled under this plan.

        The cohort query: it is what makes grandfathering observable, and what UC-31 walks
        to find graduation candidates.
        """
        ...


@runtime_checkable
class ConfigurationRepository(Protocol):
    """System-wide settings that an administrator can change at runtime.

    Distinct from :mod:`academy.config` settings, which are deployment concerns fixed at
    startup. The age of majority is a *business* parameter: it is set by an administrative
    employee (UC-05), it changes who has a guardian the moment it is saved, and it therefore
    has to live in the database rather than in an environment variable.
    """

    async def age_of_majority(self) -> AgeOfMajority:
        """The current global age of majority.

        Returns:
            The configured value. Implementations must return a documented default rather
            than ``None`` when nothing has been set: every guardianship check depends on
            this, and a system that cannot answer it can answer nothing about access.
        """
        ...

    async def set_age_of_majority(self, age: AgeOfMajority) -> None:
        """Set the global age of majority."""
        ...


@runtime_checkable
class ImportJobRepository(Repository[ImportJob, JobId], Protocol):
    """Queued and completed import jobs."""

    async def claim_next_pending(self) -> ImportJob | None:
        """Atomically take the oldest pending job and mark it running.

        Claiming and marking must be a **single atomic step**. Two workers polling the same
        queue will otherwise both read the same pending job and both run the import, which
        for an idempotent importer is merely wasteful and for any other kind is data
        corruption.

        Returns:
            The claimed job, already in the ``RUNNING`` state, or ``None`` if none is pending.
        """
        ...

    async def with_status(self, status: JobStatus) -> list[ImportJob]:
        """Every job currently in this state, oldest first."""
        ...

    async def submitted_by(self, person_id: PersonId) -> list[ImportJob]:
        """Every job this person submitted, newest first.

        Backs the "my imports" screen. Scoping by submitter is not authorization -- the
        ``AccessGuard`` still decides -- but it is what makes the default listing useful.
        """
        ...
