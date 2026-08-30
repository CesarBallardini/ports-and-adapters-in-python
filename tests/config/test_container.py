"""Unit tests for the composition root (ADR-0015).

`unit` tier per ADR-0013: the wiring is real, the adapters are the in-memory ones, and nothing
is mocked. What these assert is the thing a container library would otherwise be trusted to do
-- that the graph is complete, that a scope hands out ports rather than adapters, and that the
two lifetimes are the ones the ADR describes.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from academy.adapters.outbound.system import FixedClock, SystemClock
from academy.application.commands import RecordGradeCommand
from academy.application.dtos import Actor
from academy.application.errors import AuthorizationError
from academy.application.ports.inbound.grading import ManageGrades
from academy.config import ENV_PERSISTENCE, ConfigurationError, Container, Defaults, PersistenceBackend, Settings
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
    # ACADEMY_DATABASE_URL arrives with the SQLAlchemy adapter. Until then it is the
    # integration suite's business alone, and setting it must not change how a process runs.
    settings = Settings.from_env({'ACADEMY_DATABASE_URL': 'postgresql+asyncpg://db/academy'})

    assert settings == Settings()


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
def test_a_backend_without_an_adapter_fails_at_startup() -> None:
    # Delete this test when the SQLAlchemy adapter lands in Phase B: the point of it is that
    # the refusal happens while the process is starting, not on the first request.
    with pytest.raises(ConfigurationError) as failure:
        Container(Settings(persistence=PersistenceBackend.SQLALCHEMY))

    # Naming the variable matters as much as refusing: whoever reads this line in a crash log
    # is looking for what to change, not for which of our classes noticed.
    assert ENV_PERSISTENCE in str(failure.value)


@pytest.mark.unit
def test_a_container_starts_from_an_empty_environment() -> None:
    # The claim a container image makes: nothing mounted, nothing exported, it still runs.
    assert Container.from_env({}).settings == Settings()


@pytest.mark.unit
def test_the_environment_reaches_the_container_not_just_the_settings() -> None:
    with pytest.raises(ConfigurationError):
        Container.from_env({'ACADEMY_PERSISTENCE': 'sqlalchemy'})


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
