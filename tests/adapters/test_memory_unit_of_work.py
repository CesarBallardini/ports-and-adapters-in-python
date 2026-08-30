"""Unit tests for the in-memory unit of work's rollback.

`unit` tier per ADR-0013. These are adapter tests rather than use-case tests, and they exist
because the in-memory adapter is a production-grade adapter (ADR-0014): its transaction
semantics are asserted, not assumed. Most of them would pass against a store that snapshotted
itself wholesale; the first one is the reason it does not.
"""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from academy.adapters.outbound.persistence.memory import (
    DEFAULT_AGE_OF_MAJORITY,
    MemoryConfigurationRepository,
    MemoryPersonRepository,
    MemoryStore,
    MemoryUnitOfWork,
)
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.shared.ids import PersonId

ANN = PersonId(UUID(int=1))
BEA = PersonId(UUID(int=2))


def _person(person_id: PersonId, handle: str, full_name: str | None = None) -> Person:
    return Person(
        id=person_id,
        email=Email(f'{handle}@academy.test'),
        personal=PersonalData(full_name=full_name or handle.title(), birth_date=date(1990, 1, 1)),
    )


@pytest.fixture
def store() -> MemoryStore:
    """An empty store."""
    return MemoryStore()


@pytest.mark.unit
async def test_a_rollback_leaves_another_transactions_commit_alone(store: MemoryStore) -> None:
    # The case a whole-store snapshot gets wrong, and the reason rollback is per row. Two
    # transactions overlap; one commits inside the other's block, and the other then rolls
    # back. Restoring "the store as it was when I started" would discard the commit -- with no
    # error, no trace, and nobody to notice until the data was missing.
    people = MemoryPersonRepository(store)
    committed = asyncio.Event()

    async def commits() -> None:
        uow = MemoryUnitOfWork(store)
        async with uow:
            await people.add(_person(ANN, 'ann'))
            await uow.commit()
        committed.set()

    async def rolls_back() -> None:
        uow = MemoryUnitOfWork(store)
        async with uow:
            await people.add(_person(BEA, 'bea'))
            await committed.wait()
            await uow.rollback()

    await asyncio.gather(rolls_back(), commits())

    assert await people.get(ANN) is not None
    assert await people.get(BEA) is None


@pytest.mark.unit
async def test_a_rollback_leaves_a_row_another_transaction_committed(store: MemoryStore) -> None:
    # The same row this time, which the first version of the per-row journal still got wrong:
    # it restored unconditionally, so the loser's rollback reinstated its own predecessor over
    # a committed value. An undo entry now also remembers what the write left there, and puts
    # the row back only while it still holds it.
    people = MemoryPersonRepository(store)
    await people.add(_person(ANN, 'ann'))
    committed = asyncio.Event()

    async def commits() -> None:
        uow = MemoryUnitOfWork(store)
        async with uow:
            await people.save(_person(ANN, 'ann', 'Committed'))
            await uow.commit()
        committed.set()

    async def rolls_back() -> None:
        uow = MemoryUnitOfWork(store)
        async with uow:
            await people.save(_person(ANN, 'ann', 'Rolled Back'))
            await committed.wait()
            await uow.rollback()

    await asyncio.gather(rolls_back(), commits())

    stored = await people.get(ANN)
    assert stored is not None
    assert stored.personal.full_name == 'Committed'


@pytest.mark.unit
async def test_overlapping_transactions_are_atomic_but_not_isolated(store: MemoryStore) -> None:
    # A characterisation test: this pins a *limitation*, not a guarantee. The backend takes no
    # locks and keeps no read view, so one transaction sees another's uncommitted delete and
    # claims the email it freed. When the delete rolls back, both people hold it -- a state
    # `add` and `save` exist to prevent.
    #
    # Fixing it needs a lock held across await boundaries, which would turn the nesting the
    # store refuses into a deadlock. The honest boundary is that this adapter gives atomicity
    # and the SQLAlchemy one gives isolation; the day that stops being true, this test fails
    # and says so.
    people = MemoryPersonRepository(store)
    await people.add(_person(ANN, 'shared'))
    deleted = asyncio.Event()
    reused = asyncio.Event()

    async def deletes_then_rolls_back() -> None:
        uow = MemoryUnitOfWork(store)
        async with uow:
            await people.delete(ANN)
            deleted.set()
            await reused.wait()
            await uow.rollback()

    async def claims_the_freed_email() -> None:
        await deleted.wait()
        uow = MemoryUnitOfWork(store)
        async with uow:
            await people.add(_person(BEA, 'shared'))
            await uow.commit()
        reused.set()

    await asyncio.gather(deletes_then_rolls_back(), claims_the_freed_email())

    emails = [person.email.value for person in await people.list_all()]
    assert emails == ['shared@academy.test', 'shared@academy.test']


@pytest.mark.unit
async def test_nesting_two_units_of_work_over_one_store_is_refused(store: MemoryStore) -> None:
    # Two *different* units of work used to nest silently, and the inner one then captured
    # the outer's writes and undid them on its own rollback. Two nested request_scope()
    # blocks are exactly that shape, so the refusal is loud rather than subtle.
    async with MemoryUnitOfWork(store):
        with pytest.raises(RuntimeError, match='already open'):
            await MemoryUnitOfWork(store).__aenter__()


@pytest.mark.unit
async def test_a_rollback_restores_what_a_row_held_before(store: MemoryStore) -> None:
    people = MemoryPersonRepository(store)
    await people.add(_person(ANN, 'ann'))

    async with MemoryUnitOfWork(store):
        await people.save(_person(ANN, 'ann', 'Someone Else'))

    stored = await people.get(ANN)
    assert stored is not None
    assert stored.personal.full_name == 'Ann'


@pytest.mark.unit
async def test_a_rollback_puts_a_deleted_row_back(store: MemoryStore) -> None:
    people = MemoryPersonRepository(store)
    await people.add(_person(ANN, 'ann'))

    uow = MemoryUnitOfWork(store)
    async with uow:
        await people.delete(ANN)

    assert await people.get(ANN) is not None


@pytest.mark.unit
async def test_a_row_written_twice_goes_back_to_what_it_held_first(store: MemoryStore) -> None:
    # Undo entries replay in reverse, so the earliest value wins. A log that replayed forwards
    # would leave the row holding the *second* write's predecessor, which is the first write.
    people = MemoryPersonRepository(store)
    await people.add(_person(ANN, 'ann'))

    async with MemoryUnitOfWork(store):
        for name in ('First Change', 'Second Change'):
            await people.save(_person(ANN, 'ann', name))

    stored = await people.get(ANN)
    assert stored is not None
    assert stored.personal.full_name == 'Ann'


@pytest.mark.unit
async def test_a_rollback_after_a_commit_changes_nothing(store: MemoryStore) -> None:
    people = MemoryPersonRepository(store)

    uow = MemoryUnitOfWork(store)
    async with uow:
        await people.add(_person(ANN, 'ann'))
        await uow.commit()
        # Twice, because a dry-run import rolls back unconditionally rather than asking
        # whether anything was written.
        await uow.rollback()
        await uow.rollback()

    assert await people.get(ANN) is not None


@pytest.mark.unit
async def test_a_write_outside_a_unit_of_work_is_immediate(store: MemoryStore) -> None:
    # Seeding a store does this, and there is nothing to roll back to. A later transaction
    # that rolls back must not reach behind itself and undo it.
    people = MemoryPersonRepository(store)
    await people.add(_person(ANN, 'ann'))

    async with MemoryUnitOfWork(store):
        await people.add(_person(BEA, 'bea'))

    assert await people.get(ANN) is not None
    assert await people.get(BEA) is None


@pytest.mark.unit
async def test_a_failed_transaction_leaves_the_configuration_alone(store: MemoryStore) -> None:
    # The age of majority is a single row rather than a table, and journalled the same way:
    # every guardianship check in the system reads it, so a half-applied change to it would
    # silently change who may see whose records.
    configuration = MemoryConfigurationRepository(store)

    with pytest.raises(RuntimeError):
        async with MemoryUnitOfWork(store):
            await configuration.set_age_of_majority(AgeOfMajority(21))
            raise RuntimeError('boom')

    assert await configuration.age_of_majority() == DEFAULT_AGE_OF_MAJORITY


@pytest.mark.unit
async def test_entering_the_same_unit_of_work_twice_is_refused(store: MemoryStore) -> None:
    uow = MemoryUnitOfWork(store)

    async with uow:
        with pytest.raises(RuntimeError):
            await uow.__aenter__()


@pytest.mark.unit
async def test_a_unit_of_work_can_be_used_again_after_it_ends(store: MemoryStore) -> None:
    # The scope builds one unit of work per request, but nothing stops a CLI command from
    # reusing one across two operations, and a transaction that leaked its previous entry
    # would refuse the second.
    people = MemoryPersonRepository(store)
    uow = MemoryUnitOfWork(store)

    async with uow:
        await people.add(_person(ANN, 'ann'))
        await uow.commit()

    async with uow:
        await people.add(_person(BEA, 'bea'))

    assert await people.get(ANN) is not None
    assert await people.get(BEA) is None
