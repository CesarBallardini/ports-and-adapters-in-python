"""Unit tests for the composition root (ADR-0015).

`unit` tier per ADR-0013: the wiring is real, the adapters are the in-memory ones, and nothing
is mocked. What these assert is the thing a container library would otherwise be trusted to do
-- that the graph is complete, that a scope hands out ports rather than adapters, and that the
two lifetimes are the ones the ADR describes.
"""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from academy.adapters.outbound.identity import RepositoryActorIdentity, StaticActorIdentity
from academy.adapters.outbound.persistence.sqlalchemy.session import migrate_to_head
from academy.adapters.outbound.system import FixedClock, SystemClock
from academy.application.commands import RecordGradeCommand, SubmitImportCommand, ViewAcademicHistoryCommand
from academy.application.dtos import Actor, ImportResultDto
from academy.application.errors import AuthorizationError
from academy.application.jobs import ImportJob, ImportKind, JobStatus
from academy.application.ports.inbound.grading import ManageGrades
from academy.application.ports.inbound.imports import ImportData
from academy.application.ports.inbound.records import ViewStudentRecords
from academy.config import (
    ENV_BOOTSTRAP_ADMIN,
    ENV_IDENTITY,
    ENV_PERSISTENCE,
    ENV_SECRET_KEY,
    AsgiApplication,
    ConfigurationError,
    Container,
    Defaults,
    IdentityBackend,
    PersistenceBackend,
    Settings,
    create_app,
)
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.services.grading_service import StudentNotEnrolledError
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

TODAY = date(2026, 8, 30)
TERM = Term(2026, 1)

TEACHER = PersonId(UUID(int=1))
STUDENT = PersonId(UUID(int=2))
OUTSIDER = PersonId(UUID(int=3))
# The id a `static` identity is configured with: an administrator who has no person record,
# which is the state a freshly migrated database is in (ADR-0022).
BOOTSTRAP = PersonId(UUID(int=4))
SECTION = SectionId(UUID(int=10))
# Same teacher, nobody enrolled: lets a grade be recorded for a student the teacher does teach,
# in a section that student is not in -- which fails inside the transaction, after a write.
EMPTY_SECTION = SectionId(UUID(int=11))
MATH = SubjectId(UUID(int=20))
PHYSICS = SubjectId(UUID(int=21))

TEACHER_ACTOR = Actor(person_id=TEACHER, roles=frozenset({Role.TEACHER}))
OUTSIDER_ACTOR = Actor(person_id=OUTSIDER, roles=frozenset())


def _person(person_id: PersonId, name: str, born: date, *roles: Role) -> Person:
    local = name.split()[0].lower()
    return Person(
        id=person_id,
        email=Email(f'{local}@academy.test'),
        personal=PersonalData(full_name=name, birth_date=born),
        roles=set(roles),
    )


@pytest.fixture
def container() -> Container:
    """A container on the in-memory backend, with time stopped so nothing depends on today."""
    return Container(
        Settings(persistence=PersistenceBackend.MEMORY),
        clock=FixedClock(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=UTC)),
    )


async def _seed(container: Container) -> None:
    """Put a teacher, a student, an outsider and two sections into the store."""
    async with container.request_scope() as scope:
        await scope.people.add(_person(TEACHER, 'Grace Hopper', date(1980, 1, 1), Role.TEACHER))
        await scope.people.add(_person(STUDENT, 'Ada Lovelace', date(2005, 5, 1), Role.STUDENT))
        await scope.people.add(_person(OUTSIDER, 'Nemo Nobody', date(1990, 1, 1)))
        section = CourseSection(id=SECTION, subject_id=MATH, term=TERM, teacher_id=TEACHER)
        section.enroll(STUDENT)
        await scope.sections.add(section)
        await scope.sections.add(CourseSection(id=EMPTY_SECTION, subject_id=PHYSICS, term=TERM, teacher_id=TEACHER))


@pytest.mark.unit
def test_an_empty_environment_takes_every_default() -> None:
    # The two must agree by construction: `Settings()` is what the fields say, and
    # `from_env({})` is what a process with no environment gets.
    assert Settings.from_env({}) == Settings()


@pytest.mark.unit
def test_every_field_default_comes_from_the_defaults_class() -> None:
    assert Settings().persistence is Defaults.PERSISTENCE


@pytest.mark.unit
def test_the_default_backend_is_the_one_with_an_adapter() -> None:
    # Pins the value itself, not just the indirection: a default that quietly became
    # `SQLALCHEMY` would leave a bare `make run` unable to start.
    assert Defaults.PERSISTENCE is PersistenceBackend.MEMORY


@pytest.mark.unit
def test_two_configurations_compare_by_what_they_say() -> None:
    assert Settings(persistence=PersistenceBackend.MEMORY) == Settings(persistence=PersistenceBackend.MEMORY)
    assert Settings(persistence=PersistenceBackend.MEMORY) != Settings(persistence=PersistenceBackend.SQLALCHEMY)
    assert Settings() != PersistenceBackend.MEMORY
    assert len({Settings(), Settings()}) == 1


@pytest.mark.unit
def test_the_repr_names_every_datum() -> None:
    # A startup log line that omitted a datum would be worse than none: it would look like
    # the whole configuration.
    assert 'persistence' in repr(Settings())
    assert PersistenceBackend.MEMORY.value in repr(Settings())


@pytest.mark.unit
def test_a_configuration_cannot_be_edited_after_it_is_built() -> None:
    settings = Settings()

    # Called through the dunder because the direct assignment this stands for is a type error
    # -- which is the first line of defence, and this is the second: at runtime a datum is a
    # property with no setter, so a deployment's configuration is fixed once the process is up.
    with pytest.raises(AttributeError):
        settings.__setattr__('persistence', PersistenceBackend.SQLALCHEMY)

    assert settings.persistence is Defaults.PERSISTENCE


@pytest.mark.unit
def test_settings_come_from_the_environment() -> None:
    settings = Settings.from_env({'ACADEMY_PERSISTENCE': 'sqlalchemy'})

    assert settings.persistence is PersistenceBackend.SQLALCHEMY


@pytest.mark.unit
def test_a_variable_the_settings_do_not_define_is_ignored() -> None:
    # Something plausible that this system has no setting for. Setting it must not change how
    # a process runs, and must not be an error either -- an environment is shared, and half of
    # what is in it belongs to something else.
    settings = Settings.from_env({'ACADEMY_LOG_LEVEL': 'debug'})

    assert settings == Settings()


@pytest.mark.unit
def test_the_application_reads_its_own_database_url() -> None:
    settings = Settings.from_env({'ACADEMY_DATABASE_URL': 'postgresql+asyncpg://app@db/academy'})

    assert settings.database_url == 'postgresql+asyncpg://app@db/academy'


@pytest.mark.unit
def test_migrations_use_their_own_url_when_one_is_given() -> None:
    # Two roles, because migrations own the schema and the application owns the data
    # (ADR-0018). The application's URL must not become the migrator's by accident.
    settings = Settings.from_env(
        {
            'ACADEMY_DATABASE_URL': 'postgresql+asyncpg://app@db/academy',
            'ACADEMY_MIGRATION_DATABASE_URL': 'postgresql+asyncpg://migrator@db/academy',
        }
    )

    assert settings.database_url == 'postgresql+asyncpg://app@db/academy'
    assert settings.migration_database_url == 'postgresql+asyncpg://migrator@db/academy'


@pytest.mark.unit
def test_migrations_fall_back_to_the_application_url() -> None:
    # What a developer on SQLite has: one URL, one file, and no roles to separate. The
    # fallback is a convenience with a cost, and the cost is that a PostgreSQL deployment
    # which forgets the second URL silently migrates as the application role.
    settings = Settings.from_env({'ACADEMY_DATABASE_URL': 'sqlite+aiosqlite:///./academy.db'})

    assert settings.migration_database_url == settings.database_url


@pytest.mark.unit
def test_an_unknown_backend_is_rejected_with_the_alternatives_named() -> None:
    with pytest.raises(ConfigurationError) as failure:
        Settings.from_env({'ACADEMY_PERSISTENCE': 'postgres'})

    assert 'memory' in str(failure.value)


@pytest.mark.unit
@pytest.mark.parametrize('blank', ['', ' ', '\t', '\n'])
def test_a_blank_variable_means_unset(blank: str) -> None:
    # `ACADEMY_PERSISTENCE=` in a compose file, an empty CI matrix leg, an exported-but-cleared
    # shell variable: all of them mean "no opinion", none of them asks for a backend named ''.
    assert Settings.from_env({ENV_PERSISTENCE: blank}) == Settings()


@pytest.mark.unit
def test_the_backend_name_survives_the_way_environments_quote_things() -> None:
    # `ACADEMY_PERSISTENCE= Memory ` out of a .env file or a compose entry is the same
    # request as `memory`, and a deployment that fails on a stray space learns nothing.
    assert Settings.from_env({'ACADEMY_PERSISTENCE': ' Memory '}).persistence is PersistenceBackend.MEMORY


@pytest.mark.unit
def test_the_backend_names_are_the_deployment_facing_contract() -> None:
    # The member names are ours to rename; these strings are not -- they are what is written
    # in a compose file, a systemd unit or a Kubernetes manifest, and changing one is a
    # breaking change to every deployment.
    assert [backend.value for backend in PersistenceBackend] == ['memory', 'sqlalchemy']


@pytest.mark.unit
async def test_the_sqlalchemy_backend_serves_the_same_ports(tmp_path: Path) -> None:
    # This replaces the test that asserted the SQLAlchemy branch *refused* to start, which was
    # written to be deleted the day the adapter landed. It has landed, and what matters now is
    # that a scope over it hands out the same ports the memory backend does -- nothing above
    # the composition root can tell which is underneath.
    url = f'sqlite+aiosqlite:///{(tmp_path / "academy_development.db").as_posix()}'
    await asyncio.to_thread(migrate_to_head, url)

    container = Container(
        Settings(
            persistence=PersistenceBackend.SQLALCHEMY,
            database_url=url,
            upload_directory=str(tmp_path / 'uploads'),
        ),
        clock=FixedClock(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=UTC)),
    )
    try:
        async with container.request_scope() as scope:
            await scope.people.add(_person(TEACHER, 'Grace Hopper', date(1980, 1, 1), Role.TEACHER))
            stored = await scope.people.get(TEACHER)

            assert stored is not None
            assert isinstance(scope.grade_management(), ManageGrades)
            assert isinstance(scope.student_records(), ViewStudentRecords)
            assert isinstance(scope.import_data(), ImportData)
    finally:
        await container.aclose()


@pytest.mark.unit
async def test_a_container_does_not_migrate_its_own_database(tmp_path: Path) -> None:
    # The application connects as a role that cannot issue DDL (ADR-0018), so it must not try.
    # Against an unmigrated database the first query fails -- which is the honest outcome, and
    # far better than a process that quietly repairs a schema two instances are racing on.
    url = f'sqlite+aiosqlite:///{(tmp_path / "never_migrated.db").as_posix()}'
    container = Container(
        Settings(persistence=PersistenceBackend.SQLALCHEMY, database_url=url),
        clock=FixedClock(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=UTC)),
    )

    try:
        with pytest.raises(OperationalError, match='no such table'):
            async with container.request_scope() as scope:
                await scope.people.get(TEACHER)
    finally:
        await container.aclose()


@pytest.mark.unit
def test_a_container_starts_from_an_empty_environment() -> None:
    # The claim a container image makes: nothing mounted, nothing exported, it still runs.
    assert Container.from_env({}).settings == Settings()


@pytest.mark.unit
def test_the_environment_reaches_the_container_not_just_the_settings(tmp_path: Path) -> None:
    # Parsing the environment correctly and then ignoring the result would pass every settings
    # test in this file.
    url = f'sqlite+aiosqlite:///{(tmp_path / "academy_development.db").as_posix()}'

    container = Container.from_env({'ACADEMY_PERSISTENCE': 'sqlalchemy', 'ACADEMY_DATABASE_URL': url})

    assert container.settings.persistence is PersistenceBackend.SQLALCHEMY
    assert container.settings.database_url == url


@pytest.mark.unit
async def test_a_scope_hands_out_the_port_not_the_adapter(container: Container) -> None:
    async with container.request_scope() as scope:
        grades = scope.grade_management()

    assert isinstance(grades, ManageGrades)


@pytest.mark.unit
async def test_the_wired_graph_records_a_grade(container: Container) -> None:
    await _seed(container)

    async with container.request_scope() as scope:
        result = await scope.grade_management().record_grade(
            RecordGradeCommand(actor=TEACHER_ACTOR, section_id=str(SECTION), student_id=str(STUDENT), grade=8)
        )

    assert result.best_grade == 8
    assert result.passed


@pytest.mark.unit
async def test_a_committed_write_outlives_the_scope_that_made_it(container: Container) -> None:
    await _seed(container)

    async with container.request_scope() as scope:
        await scope.grade_management().record_grade(
            RecordGradeCommand(actor=TEACHER_ACTOR, section_id=str(SECTION), student_id=str(STUDENT), grade=8)
        )

    # A second scope, and so a second unit of work: the store is the container's, the
    # transaction is the scope's. That split is the whole of ADR-0015's lifetime claim.
    async with container.request_scope() as scope:
        history = await scope.histories.get(STUDENT)

    assert history is not None
    assert [entry.grade.value for entry in history.entries_for(MATH)] == [8]


@pytest.mark.unit
async def test_a_failed_use_case_leaves_the_store_as_it_found_it(container: Container) -> None:
    await _seed(container)

    # The teacher does teach this student, so the guard passes and the transaction opens.
    # `get_or_create` then writes an empty history before the domain service refuses the
    # grade -- so a rollback that did not work would leave that row behind.
    async with container.request_scope() as scope:
        with pytest.raises(StudentNotEnrolledError):
            await scope.grade_management().record_grade(
                RecordGradeCommand(
                    actor=TEACHER_ACTOR, section_id=str(EMPTY_SECTION), student_id=str(STUDENT), grade=8
                )
            )

    async with container.request_scope() as scope:
        assert await scope.histories.get(STUDENT) is None


@pytest.mark.unit
async def test_the_wired_guard_refuses_an_actor_with_no_relation(container: Container) -> None:
    # Cheap, and it is the test that fails if the resolver is ever wired to different
    # repositories from the ones the use case reads: an outsider would then resolve relations
    # against an empty store and be refused for the wrong reason, or not refused at all.
    await _seed(container)

    async with container.request_scope() as scope:
        with pytest.raises(AuthorizationError):
            await scope.grade_management().record_grade(
                RecordGradeCommand(actor=OUTSIDER_ACTOR, section_id=str(SECTION), student_id=str(STUDENT), grade=8)
            )


@pytest.mark.unit
async def test_the_scope_builds_the_record_reading_use_cases(container: Container) -> None:
    await _seed(container)

    # The student themselves, not the teacher: teaching a section grants READ on its GRADES,
    # and deliberately not on a student's whole transcript.
    student_actor = Actor(person_id=STUDENT, roles=frozenset({Role.STUDENT}))

    async with container.request_scope() as scope:
        records = scope.student_records()
        history = await records.view_academic_history(
            ViewAcademicHistoryCommand(actor=student_actor, student_id=str(STUDENT))
        )

    assert isinstance(records, ViewStudentRecords)
    assert history.student_id == str(STUDENT)


@pytest.mark.unit
async def test_the_scope_builds_the_import_use_cases(container: Container) -> None:
    await _seed(container)
    sheet = b'student_email,grade\r\nada@academy.test,8\r\n'

    async with container.request_scope() as scope:
        imports = scope.import_data()
        outcome = await imports.submit(
            SubmitImportCommand(
                actor=TEACHER_ACTOR,
                kind=ImportKind.GRADE_SHEET,
                data=sheet,
                filename='grades.csv',
                content_type='text/csv',
                context={'section_id': str(SECTION)},
            )
        )

    assert isinstance(imports, ImportData)
    assert isinstance(outcome, ImportResultDto), 'a small file runs inline'
    assert outcome.created == 1


@pytest.mark.unit
async def test_the_inline_queue_runs_a_queued_job_through_the_same_service() -> None:
    # The knot the composition root ties: the queue calls the service that holds it. With a
    # threshold of one byte every upload is queued, and the inline queue runs it before submit
    # returns -- so the job comes back already done.
    container = Container(
        Settings(import_inline_threshold_bytes=1),
        clock=FixedClock(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=UTC)),
    )
    await _seed(container)
    sheet = b'student_email,grade\r\nada@academy.test,8\r\n'

    async with container.request_scope() as scope:
        outcome = await scope.import_data().submit(
            SubmitImportCommand(
                actor=TEACHER_ACTOR,
                kind=ImportKind.GRADE_SHEET,
                data=sheet,
                filename='grades.csv',
                content_type='text/csv',
                context={'section_id': str(SECTION)},
            )
        )
        assert isinstance(outcome, ImportJob)
        stored = await scope.jobs.get(outcome.id)

    assert stored is not None
    assert stored.status is JobStatus.DONE
    assert stored.result is not None
    assert stored.result.created == 1


@pytest.mark.unit
async def test_each_scope_gets_its_own_unit_of_work(container: Container) -> None:
    async with container.request_scope() as first, container.request_scope() as second:
        assert first.unit_of_work() is not second.unit_of_work()


@pytest.mark.unit
async def test_every_use_case_gets_its_own_unit_of_work(container: Container) -> None:
    # A unit of work refuses re-entry while it is active, so a scope that handed the same one
    # to every use case would turn two overlapping calls into a RuntimeError rather than two
    # transactions. The factory is what prevents that.
    async with container.request_scope() as scope:
        assert scope.unit_of_work() is not scope.unit_of_work()


@pytest.mark.unit
async def test_two_containers_do_not_share_a_store(container: Container) -> None:
    # Each container owns its store, so a test, a worker and a web process in one interpreter
    # cannot see each other's data. The day this stops holding, the suite stops being able to
    # run in parallel and nothing else would say so.
    await _seed(container)
    other = Container(Settings(), clock=FixedClock.at(TODAY))

    async with other.request_scope() as scope:
        assert await scope.people.get(TEACHER) is None


@pytest.mark.unit
async def test_the_clock_is_the_containers_and_reaches_every_scope() -> None:
    clock = FixedClock.at(TODAY)
    container = Container(Settings(), clock=clock)

    async with container.request_scope() as first, container.request_scope() as second:
        assert first.clock is clock
        assert second.clock is clock


@pytest.mark.unit
async def test_the_default_clock_is_the_real_one() -> None:
    async with Container(Settings()).request_scope() as scope:
        assert isinstance(scope.clock, SystemClock)


@pytest.mark.unit
async def test_the_container_reads_the_environment_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ACADEMY_PERSISTENCE', 'memory')
    container = Container.from_env()

    monkeypatch.setenv('ACADEMY_PERSISTENCE', 'sqlalchemy')

    assert container.settings.persistence is PersistenceBackend.MEMORY


# ---------------------------------------------------------------------------------------------
# The identity axis (ADR-0022)
# ---------------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_the_default_identity_reads_the_person_record() -> None:
    """A running system resolves an actor by looking them up; that is the ordinary case."""
    async with Container(Settings()).request_scope() as scope:
        assert isinstance(scope.identity, RepositoryActorIdentity)


@pytest.mark.unit
async def test_the_static_identity_is_wired_when_a_deployment_asks_for_it() -> None:
    settings = Settings(identity=IdentityBackend.STATIC, bootstrap_admin=str(BOOTSTRAP))

    async with Container(settings).request_scope() as scope:
        assert isinstance(scope.identity, StaticActorIdentity)


@pytest.mark.unit
async def test_the_bootstrap_administrator_resolves_without_a_person_record() -> None:
    """The whole reason the second adapter exists.

    The store is empty -- nothing has been added to it -- so the repository-backed identity would
    resolve nobody at all, and there would be no way to reach the surface that creates the first
    person. This is the way in, and it is the one thing it can do.
    """
    settings = Settings(identity=IdentityBackend.STATIC, bootstrap_admin=str(BOOTSTRAP))

    async with Container(settings).request_scope() as scope:
        actor = await scope.identity.resolve(BOOTSTRAP)

    assert actor is not None
    assert actor.is_administrator


@pytest.mark.unit
async def test_the_static_identity_resolves_nobody_else() -> None:
    """One configured id, and it is not a skeleton key for every id.

    A second configured administrator would be a second thing to forget to remove, and an
    identity that answered for any id at all would be an authentication bypass.
    """
    settings = Settings(identity=IdentityBackend.STATIC, bootstrap_admin=str(BOOTSTRAP))

    async with Container(settings).request_scope() as scope:
        assert await scope.identity.resolve(PersonId(UUID(int=1234))) is None


@pytest.mark.unit
def test_a_static_identity_with_nothing_to_resolve_refuses_to_start() -> None:
    """At startup, because such a process can serve no authenticated request at all."""
    with pytest.raises(ConfigurationError) as failure:
        Container(Settings(identity=IdentityBackend.STATIC))

    assert ENV_BOOTSTRAP_ADMIN in str(failure.value)


@pytest.mark.unit
def test_a_bootstrap_id_that_is_not_a_uuid_refuses_to_start() -> None:
    """Named, so the fix is obvious without reading the composition root."""
    with pytest.raises(ConfigurationError) as failure:
        Container(Settings(identity=IdentityBackend.STATIC, bootstrap_admin='dana@example.edu'))

    assert 'not a UUID' in str(failure.value)


@pytest.mark.unit
def test_an_unknown_identity_backend_is_rejected_with_the_alternatives_named() -> None:
    with pytest.raises(ConfigurationError) as failure:
        Settings.from_env({ENV_IDENTITY: 'ldap'})

    assert 'repository' in str(failure.value)
    assert 'static' in str(failure.value)


@pytest.mark.unit
def test_the_identity_axis_is_independent_of_the_persistence_axis() -> None:
    """Which database is underneath says nothing about how a person id becomes an actor.

    A deployment that had to change both together is one where the bootstrap case -- a durable,
    migrated, empty database -- could not exist.
    """
    settings = Settings.from_env(
        {
            ENV_PERSISTENCE: 'sqlalchemy',
            ENV_IDENTITY: 'static',
            ENV_BOOTSTRAP_ADMIN: str(BOOTSTRAP),
        }
    )

    assert settings.persistence is PersistenceBackend.SQLALCHEMY
    assert settings.identity is IdentityBackend.STATIC


# ---------------------------------------------------------------------------------------------
# The signing key
# ---------------------------------------------------------------------------------------------


# A literal in a test, not a credential: bandit and ruff cannot tell them apart, and the check
# earns its keep everywhere else.
CHOSEN_KEY = 'chosen-by-the-deployment'  # noqa: S105


@pytest.mark.unit
def test_a_deployment_that_named_a_key_gets_that_key() -> None:
    assert Container(Settings(secret_key=CHOSEN_KEY)).secret_key == CHOSEN_KEY


@pytest.mark.unit
def test_an_in_memory_deployment_gets_a_generated_key() -> None:
    """Nothing survives a restart there anyway, so a session that does not either is consistent.

    It is also what makes ``make run`` and the whole test suite work with no environment at all.
    """
    container = Container(Settings())

    assert len(container.secret_key) >= 32


@pytest.mark.unit
def test_a_generated_key_is_fixed_once_it_has_been_used() -> None:
    """Otherwise every request would sign with a different key and nothing would verify."""
    container = Container(Settings())

    assert container.secret_key == container.secret_key


@pytest.mark.unit
def test_two_processes_get_different_generated_keys() -> None:
    """Which is exactly why a durable deployment may not have one generated for it."""
    assert Container(Settings()).secret_key != Container(Settings()).secret_key


@pytest.mark.unit
def test_a_durable_deployment_without_a_key_is_refused_when_the_key_is_needed() -> None:
    """Deferred to first use, not raised at construction, and the distinction is load-bearing.

    A CLI invocation and an import worker have no sessions to sign. Demanding a signing key from
    them would mean a deployment of either had to invent one to satisfy a check for a surface it
    does not run.
    """
    container = Container(Settings(persistence=PersistenceBackend.SQLALCHEMY))

    with pytest.raises(ConfigurationError) as failure:
        _ = container.secret_key

    assert ENV_SECRET_KEY in str(failure.value)


@pytest.mark.unit
async def test_a_durable_deployment_without_a_key_still_serves_the_cli() -> None:
    """The other half: nothing that does not ask for a key is troubled by its absence."""
    container = Container(Settings(persistence=PersistenceBackend.SQLALCHEMY))
    try:
        async with container.request_scope() as scope:
            assert scope.grade_management() is not None
    finally:
        await container.aclose()


@pytest.mark.unit
def test_the_asgi_factory_returns_something_uvicorn_can_serve() -> None:
    """``create_app``'s return type is a promise, and this is it kept.

    The signature says :class:`AsgiApplication` rather than ``FastAPI`` because naming the class
    would import it at module scope and undo the lazy import the CLI depends on. That makes the
    annotation a structural claim about a type the file never names -- exactly the kind that goes
    stale silently -- so it is checked at run time as well as by two type checkers.
    """
    application = create_app({})

    assert isinstance(application, AsgiApplication)
