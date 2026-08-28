"""Age of majority value object."""

from __future__ import annotations

from dataclasses import dataclass

from academy.domain.shared.errors import DomainError


class InvalidAgeOfMajorityError(DomainError):
    """Raised when the configured age of majority is not a positive number of years."""


@dataclass(frozen=True, slots=True)
class AgeOfMajority:
    """The single, global age of majority, expressed in whole years."""

    years: int

    def __post_init__(self) -> None:
        """Validate that the age of majority is positive."""
        if self.years <= 0:
            raise InvalidAgeOfMajorityError(self.years)

    def is_reached_at(self, age: int) -> bool:
        """Return whether an ``age`` (in years) meets or exceeds the age of majority."""
        return age >= self.years
