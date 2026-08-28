"""Academic term value object."""

from __future__ import annotations

from dataclasses import dataclass

from academy.domain.shared.errors import DomainError

_TERMS_PER_YEAR = (1, 2)


class InvalidTermError(DomainError):
    """Raised when a term has a non-positive year or an out-of-range number."""


@dataclass(frozen=True, slots=True, order=True)
class Term:
    """An academic term: two four-month terms per year, ordered by (year, number)."""

    year: int
    number: int

    def __post_init__(self) -> None:
        """Validate the year is positive and the number is 1 or 2."""
        if self.year <= 0:
            raise InvalidTermError(f'year must be positive: {self.year}')
        if self.number not in _TERMS_PER_YEAR:
            raise InvalidTermError(f'number must be one of {_TERMS_PER_YEAR}: {self.number}')

    def label(self) -> str:
        """Return the canonical label, e.g. ``2026-T1``."""
        return f'{self.year}-T{self.number}'
