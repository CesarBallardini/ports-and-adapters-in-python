"""What every identifier generator must do (ADR-0014).

A port with two implementations and, until now, no shared assertions — which is exactly the
situation Rule 4 exists to catch: the pair was written to be interchangeable and nothing checked
that they are.

The two differ in the one way that matters to a caller and in nothing else: one is random and
one is reproducible. Everything below is true of both, and the couple of properties that are
true of only one are asserted separately at the bottom, where they read as the deliberate
difference rather than as a contract violation.
"""

from collections.abc import Callable
from uuid import UUID

import pytest

from academy.adapters.outbound.system.ids import SequentialIdGenerator, Uuid4IdGenerator
from academy.application.jobs import JobId
from academy.application.ports.outbound.system import IdGenerator
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

# Every method on the port paired with the type it must produce, so a new identifier cannot be
# added to the port and quietly left untested for one of the adapters.
#
FACTORIES = [
    ('next_person_id', PersonId),
    ('next_credential_id', CredentialId),
    ('next_program_id', ProgramId),
    ('next_plan_id', PlanId),
    ('next_subject_id', SubjectId),
    ('next_section_id', SectionId),
    ('next_graduation_id', GraduationId),
    ('next_guardianship_id', GuardianshipId),
    ('next_job_id', JobId),
]

BACKENDS = [
    pytest.param(Uuid4IdGenerator, id='uuid4'),
    pytest.param(SequentialIdGenerator, id='sequential'),
]


@pytest.fixture(params=BACKENDS)
def ids(request: pytest.FixtureRequest) -> IdGenerator:
    """One generator, fresh."""
    build: Callable[[], IdGenerator] = request.param
    return build()


@pytest.mark.unit
@pytest.mark.parametrize(('factory', 'expected'), FACTORIES)
def test_every_identifier_is_new(ids: IdGenerator, factory: str, expected: type) -> None:
    # The port's flat promise: never return a value it has returned before. A hundred is enough
    # to catch a generator that reuses, and cheap enough to run for every method of both.
    produced = {getattr(ids, factory)() for _ in range(100)}

    assert len(produced) == 100


@pytest.mark.unit
@pytest.mark.parametrize(('factory', 'expected'), FACTORIES)
def test_an_identifier_is_of_the_type_its_method_names(ids: IdGenerator, factory: str, expected: type) -> None:
    # One method per identifier type rather than a generic `next_id()`, precisely so a
    # SubjectId cannot be handed to something expecting a PersonId. This is that promise, and
    # for the domain's ids it holds at run time as well as at type-check time.
    assert type(getattr(ids, factory)()) is expected


@pytest.mark.unit
def test_two_identifiers_of_different_types_are_never_the_same_object(ids: IdGenerator) -> None:
    assert ids.next_person_id() != ids.next_subject_id()


@pytest.mark.unit
def test_generating_an_identifier_needs_no_database(ids: IdGenerator) -> None:
    # The port promises identity *before* an entity is stored, which is what lets a use case
    # build a whole aggregate, derive a storage key from its id, and save once. A generator
    # that round-tripped to a sequence would break that and could not be sync (ADR-0005).
    assert ids.next_person_id() is not None


# --------------------------------------------------------------------------------------
# Where the two deliberately differ
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_sequential_generator_is_reproducible() -> None:
    # The property a fixture load or a deterministic demo dataset wants: two runs of the same
    # code produce the same ids, so a test can assert *which* id a use case used.
    one = SequentialIdGenerator()
    other = SequentialIdGenerator()

    assert [str(one.next_person_id()) for _ in range(3)] == [str(other.next_person_id()) for _ in range(3)]


@pytest.mark.unit
def test_the_sequential_generator_starts_where_it_was_told() -> None:
    ids = SequentialIdGenerator(start=41)

    assert ids.next_person_id() == PersonId(UUID(int=41))


@pytest.mark.unit
def test_the_sequential_generator_counts_each_type_separately() -> None:
    # The first person and the first subject are both number one. A shared counter would make
    # a fixture's expected ids depend on how many unrelated entities were created first.
    ids = SequentialIdGenerator()

    assert ids.next_person_id() == PersonId(UUID(int=1))
    assert ids.next_subject_id() == SubjectId(UUID(int=1))
    assert ids.next_person_id() == PersonId(UUID(int=2))


@pytest.mark.unit
def test_the_random_generator_is_not_reproducible() -> None:
    # Stated as an assertion because it is a security property, not an accident: a predictable
    # id in a URL is an enumerable one.
    one = Uuid4IdGenerator()
    other = Uuid4IdGenerator()

    assert one.next_person_id() != other.next_person_id()
