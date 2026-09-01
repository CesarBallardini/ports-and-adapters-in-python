"""The JSON surface: the same use cases, answered for a machine.

This router exists to make a claim falsifiable rather than to serve a second audience. ADR-0011
says the browser UI and the JSON API "call identical objects", and the way to know that is true
is to write both over one port and demand the same outcome from each. That is what
``tests/integration/test_web.py`` does: the same grade, recorded once through a form with a
session cookie and once through JSON with a bearer token, and the same standing back both times.

Everything that differs between this file and ``grades.py`` is presentation:

* it marks :attr:`~academy.adapters.inbound.web.rendering.Surface.API`, so the error boundary
  renders a failure as JSON rather than as a page;
* it takes a bearer token rather than a cookie, and therefore no CSRF check (see
  :mod:`academy.adapters.inbound.web.csrf` for why that is a reason and not an omission);
* it returns dictionaries rather than templates.

Nothing that differs is a business rule, and there is no second copy of one here to drift.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Body, Depends

from academy.adapters.inbound.web.dependencies import CurrentActor, Grades, api_surface
from academy.application.commands import ListSectionGradesCommand, RecordGradeCommand
from academy.application.dtos import GradeRecordedDto, SectionGradeRowDto, SectionGradesDto

# Every JSON path lives under this, which is a convenience for whoever mounts a reverse proxy and
# is *not* how the error boundary decides anything -- that is the router's surface marking, so a
# route moved to another prefix keeps rendering correctly.
PREFIX: Final = '/api'

router = APIRouter(prefix=PREFIX, dependencies=[Depends(api_surface)], tags=['api'])

# The JSON shape, spelled out rather than derived from the DTOs with `asdict`. A DTO is an
# internal contract that may be renamed or widened; this is a published one that may not, and
# generating it from the other would make every DTO field name a breaking change waiting to
# happen.
type JsonRow = dict[str, str | int | bool | None]


def _row(row: SectionGradeRowDto) -> JsonRow:
    """One student's line, as JSON."""
    return {
        'student_id': row.student_id,
        'full_name': row.full_name,
        'best_grade': row.best_grade,
        'passed': row.passed,
        'attempts': row.attempts,
    }


@router.get('/sections/{section_id}/grades')
async def list_grades(section_id: str, grades: Grades, actor: CurrentActor) -> dict[str, object]:
    """A section's roster with each student's standing (UC-21)."""
    sheet: SectionGradesDto = await grades.list_section_grades(
        ListSectionGradesCommand(actor=actor, section_id=section_id)
    )
    return {
        'section_id': sheet.section_id,
        'subject_id': sheet.subject_id,
        'term': sheet.term,
        'rows': [_row(row) for row in sheet.rows],
    }


@router.post('/sections/{section_id}/grades')
async def record_grade(
    section_id: str,
    grades: Grades,
    actor: CurrentActor,
    student_id: Annotated[str, Body()],
    grade: Annotated[int, Body()],
) -> dict[str, object]:
    """Record one grade and answer with the resulting standing (UC-22).

    The standing and not an acknowledgement, for the reason the DTO gives: a 4 recorded after a 7
    changes nothing about whether the subject is passed, and a caller that had to ask again to
    find that out would be a caller that sometimes does not.
    """
    recorded: GradeRecordedDto = await grades.record_grade(
        RecordGradeCommand(actor=actor, section_id=section_id, student_id=student_id, grade=grade)
    )
    return {
        'student_id': recorded.student_id,
        'subject_id': recorded.subject_id,
        'recorded_grade': recorded.recorded_grade,
        'best_grade': recorded.best_grade,
        'passed': recorded.passed,
    }
