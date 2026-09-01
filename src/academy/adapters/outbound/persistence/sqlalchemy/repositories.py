"""SQLAlchemy repository adapters over an async session.

The second implementation of every repository port, and the reason ``tests/contract`` was
written parametrised rather than against the in-memory adapter: these answer the same
assertions, so a use case tested against a dictionary is tested against PostgreSQL.

Two things shape almost every method here.

**Queries that look inside a collection filter in Python.** Enrollments, roles and held
credentials are JSON on their aggregate's row (ADR-0017), so ``for_student`` and its neighbours
load the candidate rows and filter them here. That is a real cost, recorded in the ADR: correct
on both databases, identical on both, and reading more rows than a child table would. The
alternative is dialect-specific JSON SQL in the one layer that must not differ between them.

**Existence is checked before writing.** The ports promise ``ConflictError`` for a duplicate and
``NotFoundError`` for an update of something absent, and the database's own error arrives too
late and too dialect-specific to say which. The constraint is still there and still the truth;
the check is what turns it into the answer the port names.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy import RowMapping, Table, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from academy.adapters.outbound.persistence.sqlalchemy import tables
from academy.application.errors import ConflictError, NotFoundError
from academy.application.jobs import ImportJob, ImportKind, JobId, JobStatus
from academy.application.ports.outbound.repositories import SortKey
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email, InvalidEmailError
from academy.domain.people.person import Person
from academy.domain.shared.ids import CredentialId, GuardianshipId, PersonId, SectionId, SubjectId

# The single configuration row's key. A fixed string rather than a nullable primary key,
# because a table with one row and no key invites a second row nothing would ever read.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
CONFIGURATION_KEY = 'global'
DEFAULT_AGE_OF_MAJORITY = AgeOfMajority(18)


class _SqlAlchemyRepository[EntityT, IdT](ABC):
    """The five storage operations every aggregate needs, over one mapped class.

    Subclasses say which class and which table; everything else -- the ``None``/``NotFoundError``
    asymmetry and the conflict checks -- is decided once, here, exactly as it is once in the
    in-memory adapter. Two adapters agreeing by construction is worth more than two adapters
    agreeing by review.
    """

    entity_name = 'entity'

    # The mapped attributes that hold one of ADR-0017's serialised collections, named so that
    # `save` can mark them changed.
    #
    # They have to be named, because SQLAlchemy cannot see into them. A JSON column's change
    # detection is by attribute *assignment*, and the domain never assigns: `history.record(...)`
    # appends to the list the attribute already holds, so the value the ORM compares against is
    # the mutated list itself and the comparison finds no difference. The result is a `save` that
    # returns cleanly, a `commit` that succeeds, and a row that never changed -- silent data
    # loss, and invisible to any test that reads back through the session that made the change,
    # because the identity map hands back the very object that was mutated.
    #
    # `MutableList` would track this automatically and was rejected: it needs a mutable wrapper
    # to survive every path the *domain* takes with its own list, and the domain is copied and
    # not ours to constrain (ADR-0002). Naming four attributes in the layer that knows they are
    # columns is the smaller commitment.
    mutable_collections: tuple[str, ...] = ()

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the session its scope opened.

        The session, not the engine: a repository that opened its own connection would put its
        writes outside the unit of work the use case drew.
        """
        self._session = session

    @abstractmethod
    def _entity_type(self) -> type[EntityT]:
        """The mapped class this repository stores."""

    @abstractmethod
    def _table(self) -> Table:
        """The table it is mapped to, for the queries that go through Core."""

    @abstractmethod
    def _identity(self, entity: EntityT) -> IdT:
        """The aggregate's identifier."""

    @abstractmethod
    def _sort_key(self, entity: EntityT) -> SortKey:
        """The natural key ``list_all`` orders by, ending in the id to break ties."""

    async def get(self, entity_id: IdT) -> EntityT | None:
        """Fetch one aggregate by identity, or ``None`` if no such record exists."""
        return await self._session.get(self._entity_type(), entity_id)

    async def add(self, entity: EntityT) -> None:
        """Store an aggregate that is not yet stored.

        Raises:
            ConflictError: If the identity is taken or a uniqueness constraint is violated.
        """
        identity = self._identity(entity)
        if await self.get(identity) is not None:
            raise ConflictError(f'{self.entity_name} {identity!s} already exists')

        self._session.add(entity)
        # Flushed rather than left for the commit, so that a constraint violation is raised at
        # the write that caused it and the use case can still say which row it was about.
        await self._session.flush()

    async def save(self, entity: EntityT) -> None:
        """Persist changes to an already-stored aggregate.

        Raises:
            NotFoundError: If no aggregate with that identity is stored.
            ConflictError: If the change violates a uniqueness constraint.
        """
        identity = self._identity(entity)
        if await self.get(identity) is None:
            raise NotFoundError(self.entity_name, identity)

        merged = await self._session.merge(entity)
        # Unconditionally, and before the flush. Unconditionally because there is nothing to
        # compare against -- the whole problem is that the ORM's "before" value and the caller's
        # "after" value are the same mutated object -- and because ADR-0017 already says a
        # collection is written whole, so marking one clean would save nothing a dirty one costs.
        for name in self.mutable_collections:
            flag_modified(merged, name)
        await self._session.flush()

    async def delete(self, entity_id: IdT) -> None:
        """Remove an aggregate.

        Raises:
            NotFoundError: If no aggregate with that identity is stored.
        """
        stored = await self.get(entity_id)
        if stored is None:
            raise NotFoundError(self.entity_name, entity_id)

        await self._session.delete(stored)
        await self._session.flush()

    async def list_all(self) -> list[EntityT]:
        """Every stored aggregate, in natural-key order."""
        return self._sorted(await self._all())

    async def _all(self) -> list[EntityT]:
        """Every row, unordered."""
        result = await self._session.execute(select(self._entity_type()))
        return list(result.scalars().all())

    def _sorted(self, entities: list[EntityT]) -> list[EntityT]:
        """Order a result the way the port promises.

        In Python rather than in SQL, and deliberately: the natural keys here are value objects
        -- an ``Email``, a ``Term`` -- whose ordering is the domain's, not the collation's. A
        database that sorted ``'Z'`` before ``'a'`` would satisfy an ``ORDER BY`` and fail the
        contract suite, and only one of those two is the specification.
        """
        return sorted(entities, key=self._sort_key)


class SqlAlchemyPersonRepository(_SqlAlchemyRepository[Person, PersonId]):
    """People, with their roles and held credentials, ordered by email."""

    entity_name = 'person'
    mutable_collections = ('_roles', '_held_credentials')

    def _entity_type(self) -> type[Person]:
        return Person

    def _table(self) -> Table:
        return tables.people

    def _identity(self, entity: Person) -> PersonId:
        return entity.id

    def _sort_key(self, entity: Person) -> SortKey:
        return (entity.email.value, str(entity.id))

    async def add(self, entity: Person) -> None:
        """Store a new person.

        Raises:
            ConflictError: If the identity is taken, or another person already uses this email.
        """
        await self._require_email_unused(entity)
        await super().add(entity)

    async def save(self, entity: Person) -> None:
        """Persist changes to a stored person.

        Existence is checked before uniqueness, so that saving an unknown person reports the
        unknown person rather than an email conflict with whoever holds that address.

        Raises:
            NotFoundError: If no person with that identity is stored.
            ConflictError: If another person already uses this email address.
        """
        if await self.get(entity.id) is None:
            raise NotFoundError(self.entity_name, entity.id)

        await self._require_email_unused(entity)
        await super().save(entity)

    async def _require_email_unused(self, entity: Person) -> None:
        """Enforce that email, the login identifier, is unique across people."""
        other = await self.by_email(entity.email.value)
        if other is not None and other.id != entity.id:
            raise ConflictError(f'email {entity.email.value} is already registered')

    async def by_email(self, email: str) -> Person | None:
        """Fetch the person with this email address, matching case-insensitively.

        Normalised the way ``Email`` normalises rather than turned into one: an authentication
        adapter holds whatever a user typed, and a malformed address here is a miss, not an
        error.
        """
        try:
            wanted = Email(email)
        except InvalidEmailError:
            # The port promises a miss rather than an error: an authentication adapter holds
            # whatever a user typed. The column's type would otherwise refuse to bind a plain
            # string, which is the same answer said less clearly.
            return None

        result = await self._session.execute(select(Person).where(tables.people.c.email == wanted))
        return result.scalars().one_or_none()

    async def by_ids(self, person_ids: list[PersonId]) -> list[Person]:
        """Fetch several people at once, in the order asked, omitting ids that are unknown.

        One query and a reorder, rather than one query per id: the order the caller asked for
        is not an order the database knows about, and asking it to preserve one would mean a
        ``CASE`` expression that says less than this line does.
        """
        if not person_ids:
            return []

        result = await self._session.execute(select(Person).where(tables.people.c.id.in_(person_ids)))
        found = {person.id: person for person in result.scalars().all()}
        return [person for person_id in person_ids if (person := found.get(person_id)) is not None]

    async def holders_of(self, credential_id: CredentialId) -> list[Person]:
        """Every person holding this credential.

        Filtered in Python: held credentials are a JSON column (ADR-0017), so this reads the
        table. It is the query that would justify a child table first, if the cost ever showed.
        """
        return self._sorted([p for p in await self._all() if p.holds_credential(credential_id)])


class SqlAlchemySectionRepository(_SqlAlchemyRepository[CourseSection, SectionId]):
    """Course sections and the enrollments inside them, ordered by term then subject."""

    entity_name = 'course section'
    mutable_collections = ('_enrollments',)

    def _entity_type(self) -> type[CourseSection]:
        return CourseSection

    def _table(self) -> Table:
        return tables.sections

    def _identity(self, entity: CourseSection) -> SectionId:
        return entity.id

    def _sort_key(self, entity: CourseSection) -> SortKey:
        return (entity.term.label(), str(entity.subject_id), str(entity.id))

    async def for_teacher(self, teacher_id: PersonId) -> list[CourseSection]:
        """Every section this person teaches, most recent term first."""
        result = await self._session.execute(select(CourseSection).where(tables.sections.c.teacher_id == teacher_id))
        return self._recent_first(list(result.scalars().all()))

    async def for_student(self, student_id: PersonId) -> list[CourseSection]:
        """Every section this person is enrolled in, most recent term first."""
        return self._recent_first([s for s in await self._all() if s.is_enrolled(student_id)])

    async def in_term(self, term: Term) -> list[CourseSection]:
        """Every section running in this term."""
        result = await self._session.execute(select(CourseSection).where(tables.sections.c.term == term))
        return self._sorted(list(result.scalars().all()))

    async def subjects_enrolled_by(self, student_id: PersonId) -> frozenset[SubjectId]:
        """The subjects this student already has a section for."""
        return frozenset(s.subject_id for s in await self._all() if s.is_enrolled(student_id))

    async def teaching_students_of(self, teacher_id: PersonId) -> frozenset[PersonId]:
        """Every student enrolled in any section this person teaches."""
        return frozenset(
            student_id for section in await self.for_teacher(teacher_id) for student_id in section.students()
        )

    def _recent_first(self, sections: list[CourseSection]) -> list[CourseSection]:
        """Order by term descending, ties broken by id ascending.

        Two passes rather than one reversed sort: reversing would also reverse the tiebreak,
        and the port promises a *total* order, not merely a recent-first one.
        """
        by_id = sorted(sections, key=lambda section: str(section.id))
        return sorted(by_id, key=lambda section: section.term, reverse=True)


class SqlAlchemyAcademicHistoryRepository(_SqlAlchemyRepository[AcademicHistory, PersonId]):
    """Student transcripts, keyed by the student they belong to."""

    entity_name = 'academic history'
    mutable_collections = ('_entries',)

    def _entity_type(self) -> type[AcademicHistory]:
        return AcademicHistory

    def _table(self) -> Table:
        return tables.histories

    def _identity(self, entity: AcademicHistory) -> PersonId:
        return entity.id

    def _sort_key(self, entity: AcademicHistory) -> SortKey:
        return (str(entity.id),)

    async def get_or_create(self, student_id: PersonId) -> AcademicHistory:
        """Fetch this student's transcript, storing an empty one if they have none.

        The created history is **stored**, not merely returned, so that the caller's subsequent
        ``save`` finds it. Recording a student's first grade is exactly that sequence, and an
        adapter that returned an unstored object would break on it while satisfying every other
        assertion in the contract suite.

        **Safe against a concurrent first call.** "Read, then insert" is a race whenever two
        transactions run it at once: both find nothing, both ``INSERT``, and the loser violates the
        primary key. The port promises this method never raises for a student with no grades yet,
        so losing that race has to be handled here rather than surfacing as an unclassified 500 --
        which is exactly what it did before, because ``IntegrityError`` is in no entry of
        ADR-0012's table.

        The insert therefore runs inside a **SAVEPOINT**. Without one, a failed flush leaves the
        whole transaction unusable and the caller's own work would be lost along with it; with
        one, only the failed insert is rolled back and the request continues. The winner's row is
        then read back and returned, so both callers get the same transcript and neither can tell
        which of them created it.
        """
        stored = await self.get(student_id)
        if stored is not None:
            return stored

        created = AcademicHistory(student_id)
        try:
            async with self._session.begin_nested():
                self._session.add(created)
                await self._session.flush()
        except IntegrityError:
            # Somebody inserted it between our read and our insert, so read theirs and return it.
            #
            # Nothing is expunged here, and that is not an oversight: rolling back the SAVEPOINT
            # has already evicted the losing instance from the session, so calling `expunge` on it
            # raises `InvalidRequestError: Instance is not present in this Session`. Tidying up
            # after SQLAlchemy is how this method acquired a *second* failure mode the first time
            # it was written.
            existing = await self.get(student_id)
            if existing is None:  # pragma: no cover -- the winner had not committed yet
                # Genuinely unresolvable from here: the row exists for the database and not yet
                # for this transaction. Re-raising is honest; inventing a third empty history
                # would be a second losing INSERT.
                raise
            return existing
        return created

    async def for_students(self, student_ids: list[PersonId]) -> list[AcademicHistory]:
        """Fetch several transcripts at once, in the order asked, omitting students with none."""
        if not student_ids:
            return []

        result = await self._session.execute(
            select(AcademicHistory).where(tables.histories.c.student_id.in_(student_ids))
        )
        found = {history.id: history for history in result.scalars().all()}
        return [history for student_id in student_ids if (history := found.get(student_id)) is not None]


class SqlAlchemyGuardianshipRepository(_SqlAlchemyRepository[Guardianship, GuardianshipId]):
    """Stored guardian-to-ward links.

    Storage answers only who is linked to whom. Whether a link currently *applies* is a domain
    rule computed on read, so these queries deliberately do not filter by the ward's age.
    """

    entity_name = 'guardianship'

    def _entity_type(self) -> type[Guardianship]:
        return Guardianship

    def _table(self) -> Table:
        return tables.guardianships

    def _identity(self, entity: Guardianship) -> GuardianshipId:
        return entity.id

    def _sort_key(self, entity: Guardianship) -> SortKey:
        return (str(entity.guardian_id), str(entity.ward_id), str(entity.id))

    async def wards_of(self, guardian_id: PersonId) -> list[Guardianship]:
        """Every stored guardianship where this person is the guardian."""
        result = await self._session.execute(
            select(Guardianship).where(tables.guardianships.c.guardian_id == guardian_id)
        )
        return self._sorted(list(result.scalars().all()))

    async def guardians_of(self, ward_id: PersonId) -> list[Guardianship]:
        """Every stored guardianship where this person is the ward."""
        result = await self._session.execute(select(Guardianship).where(tables.guardianships.c.ward_id == ward_id))
        return self._sorted(list(result.scalars().all()))


class SqlAlchemyConfigurationRepository:
    """System-wide settings an administrator can change at runtime.

    Core rather than the ORM, and not for the usual reason: configuration is a single row with
    no identity of its own and no domain class to map. Giving it one would invent an entity the
    domain does not have.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to its session."""
        self._session = session

    async def age_of_majority(self) -> AgeOfMajority:
        """The current global age of majority, defaulting rather than returning ``None``.

        The default is returned rather than written on first read: a read that writes would
        need a transaction, and every guardianship check calls this.
        """
        result = await self._session.execute(
            select(tables.configuration.c.age_of_majority).where(tables.configuration.c.key == CONFIGURATION_KEY)
        )
        stored = result.scalars().one_or_none()
        return stored if stored is not None else DEFAULT_AGE_OF_MAJORITY

    async def set_age_of_majority(self, age: AgeOfMajority) -> None:
        """Set the global age of majority."""
        await self._session.execute(
            delete(tables.configuration).where(tables.configuration.c.key == CONFIGURATION_KEY)
        )
        await self._session.execute(tables.configuration.insert().values(key=CONFIGURATION_KEY, age_of_majority=age))
        await self._session.flush()


class SqlAlchemyImportJobRepository:
    """Queued and completed import jobs, over Core rather than the ORM.

    The one repository here that maps by hand, and the reason is the same one ADR-0017 records
    for the domain's value objects: ``ImportJob`` is a ``slots=True`` dataclass, so SQLAlchemy
    cannot instrument it. Unlike the domain, this class is ours and a single ``weakref_slot=True``
    would make it mappable -- which is exactly why it is worth *not* doing. Adding a slot so the
    ORM can instrument an application class is the persistence layer reaching up a layer, and a
    repository that refuses that for the domain and accepts it here would be arguing two ways.

    So the mapping is fifteen lines and visible. ``_to_row`` and ``_to_job`` are the whole of it.
    """

    entity_name = 'import job'

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to the session its scope opened."""
        self._session = session

    async def get(self, entity_id: JobId) -> ImportJob | None:
        """Fetch one job by identity, or ``None`` if no such record exists."""
        result = await self._session.execute(select(tables.import_jobs).where(tables.import_jobs.c.id == entity_id))
        row = result.mappings().one_or_none()
        return None if row is None else _to_job(row)

    async def add(self, entity: ImportJob) -> None:
        """Store a job that is not yet stored.

        Raises:
            ConflictError: If the identity is taken.
        """
        if await self.get(entity.id) is not None:
            raise ConflictError(f'{self.entity_name} {entity.id!s} already exists')

        await self._session.execute(tables.import_jobs.insert().values(**_to_row(entity)))

    async def save(self, entity: ImportJob) -> None:
        """Persist changes to an already-stored job.

        Raises:
            NotFoundError: If no job with that identity is stored.
        """
        if await self.get(entity.id) is None:
            raise NotFoundError(self.entity_name, entity.id)

        await self._session.execute(
            tables.import_jobs.update().where(tables.import_jobs.c.id == entity.id).values(**_to_row(entity))
        )

    async def delete(self, entity_id: JobId) -> None:
        """Remove a job.

        Raises:
            NotFoundError: If no job with that identity is stored.
        """
        if await self.get(entity_id) is None:
            raise NotFoundError(self.entity_name, entity_id)

        await self._session.execute(delete(tables.import_jobs).where(tables.import_jobs.c.id == entity_id))

    async def list_all(self) -> list[ImportJob]:
        """Every stored job, oldest submission first."""
        result = await self._session.execute(
            select(tables.import_jobs).order_by(tables.import_jobs.c.submitted_at, tables.import_jobs.c.id)
        )
        return [_to_job(row) for row in result.mappings().all()]

    async def claim_next_pending(self) -> ImportJob | None:
        """Take the oldest pending job and mark it running, in one statement.

        A single ``UPDATE ... WHERE id = (SELECT ... LIMIT 1) ... RETURNING``, which is atomic
        on both databases and needs no explicit locking. ``SELECT ... FOR UPDATE SKIP LOCKED``
        would be the PostgreSQL idiom and SQLite does not have it -- and the port's promise is
        that claiming is atomic, not that it is implemented with a particular lock.

        Two workers therefore cannot both claim the same job: the second one's ``WHERE`` no
        longer matches, and it moves on to the next or gets nothing.

        Returns:
            The claimed job, already ``RUNNING``, or ``None`` if none is pending.
        """
        oldest = (
            select(tables.import_jobs.c.id)
            .where(tables.import_jobs.c.status == JobStatus.PENDING.value)
            .order_by(tables.import_jobs.c.submitted_at, tables.import_jobs.c.id)
            .limit(1)
            .scalar_subquery()
        )
        result = await self._session.execute(
            tables.import_jobs.update()
            .where(tables.import_jobs.c.id == oldest)
            .where(tables.import_jobs.c.status == JobStatus.PENDING.value)
            .values(status=JobStatus.RUNNING.value)
            .returning(*tables.import_jobs.c)
        )
        row = result.mappings().one_or_none()
        return None if row is None else _to_job(row)

    async def with_status(self, status: JobStatus) -> list[ImportJob]:
        """Every job currently in this state, oldest first."""
        result = await self._session.execute(
            select(tables.import_jobs)
            .where(tables.import_jobs.c.status == status.value)
            .order_by(tables.import_jobs.c.submitted_at, tables.import_jobs.c.id)
        )
        return [_to_job(row) for row in result.mappings().all()]

    async def submitted_by(self, person_id: PersonId) -> list[ImportJob]:
        """Every job this person submitted, newest first."""
        result = await self._session.execute(
            select(tables.import_jobs)
            .where(tables.import_jobs.c.submitted_by == str(person_id))
            .order_by(tables.import_jobs.c.submitted_at.desc(), tables.import_jobs.c.id)
        )
        return [_to_job(row) for row in result.mappings().all()]


def _to_row(job: ImportJob) -> dict[str, object]:
    """Take a job apart into the columns that store it."""
    return {
        'id': job.id,
        'kind': job.kind.value,
        'storage_key': job.storage_key,
        'submitted_by': job.submitted_by,
        'submitted_at': job.submitted_at,
        'status': job.status.value,
        'result': job.result,
        'failure_reason': job.failure_reason,
        'context': job.context,
    }


def _to_job(row: RowMapping) -> ImportJob:
    """Build a job from the row that stores it.

    The enums are reconstructed through their own constructors, so a status the application
    does not have raises here -- at the repository, naming the value -- rather than reaching a
    state machine that has no transition for it.
    """
    return ImportJob(
        id=row['id'],
        kind=ImportKind(row['kind']),
        storage_key=row['storage_key'],
        submitted_by=row['submitted_by'],
        submitted_at=row['submitted_at'],
        status=JobStatus(row['status']),
        result=row['result'],
        failure_reason=row['failure_reason'],
        context=dict(row['context']),
    )
