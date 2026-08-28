"""Unit tests for typed identifiers."""

from uuid import UUID

import pytest

from academy.domain.shared.ids import PersonId, SubjectId


@pytest.mark.unit
def test_from_str_roundtrips() -> None:
    raw = '00000000-0000-0000-0000-000000000001'

    assert str(PersonId.from_str(raw)) == raw


@pytest.mark.unit
def test_same_type_same_value_is_equal() -> None:
    u = UUID(int=1)

    assert PersonId(u) == PersonId(u)
    assert hash(PersonId(u)) == hash(PersonId(u))


@pytest.mark.unit
def test_different_id_types_are_not_equal_even_with_same_uuid() -> None:
    u = UUID(int=1)

    assert PersonId(u) != SubjectId(u)


@pytest.mark.unit
def test_ids_are_usable_in_sets() -> None:
    u = UUID(int=1)

    assert {PersonId(u), PersonId(u)} == {PersonId(u)}
