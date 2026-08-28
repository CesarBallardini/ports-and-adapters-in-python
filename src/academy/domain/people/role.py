"""Roles a person can hold in the system."""

from __future__ import annotations

from enum import Enum


class Role(Enum):
    """A role held by a person. A single person may hold several roles at once."""

    ADMINISTRATIVE_EMPLOYEE = 'administrative_employee'
    TEACHER = 'teacher'
    STUDENT = 'student'
    GUARDIAN = 'guardian'
