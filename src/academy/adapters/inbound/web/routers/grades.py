"""The browser's grade sheet: one page, and one row that changes.

This is the pattern ADR-0011 picked htmx for. The teacher opens a section, types a grade into a
row, and gets that row back -- not the page, not a redirect, not a JSON blob some JavaScript then
has to turn into a row. ``hx-post`` a form, receive one ``_grade_row.html``, swap it into place.

Two properties are asserted about this module elsewhere and are the reason it is worth reading:

* **It sees one driving port.** ``Grades`` is a :class:`~academy.application.ports.inbound.grading.ManageGrades`
  and nothing else -- no repositories, no scope, no way to answer a request without going through
  a use case and therefore through ``AccessGuard``.
* **It catches nothing.** There is no ``try`` in this file. A student who is not enrolled, an
  actor who does not teach here, a grade of 11 -- all of them raise, and
  :mod:`academy.adapters.inbound.web.errors` renders them from the shared table.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response

from academy.adapters.inbound.web.csrf import enforce, token_for
from academy.adapters.inbound.web.dependencies import CurrentActor, Grades, PageTemplates, web_surface
from academy.adapters.inbound.web.security import CSRF_COOKIE, SESSION_MAX_AGE_SECONDS
from academy.application.commands import ListSectionGradesCommand, RecordGradeCommand
from academy.application.dtos import SectionGradeRowDto, SectionGradesDto

# Both dependencies sit on the router rather than on each route: a route added later inherits the
# surface marking and the CSRF check instead of having to remember them, and forgetting either is
# exactly the kind of omission that is invisible until it matters.
router = APIRouter(dependencies=[Depends(web_surface), Depends(enforce)], tags=['grades'])


@router.get('/sections/{section_id}/grades')
async def grade_sheet(
    section_id: str,
    request: Request,
    grades: Grades,
    actor: CurrentActor,
    templates: PageTemplates,
) -> Response:
    """Show a section's roster with every student's standing (UC-21)."""
    sheet = await grades.list_section_grades(ListSectionGradesCommand(actor=actor, section_id=section_id))

    token = token_for(request)
    response = templates.page('grades.html', {'sheet': sheet, 'csrf_token': token.value})
    # HTTP-only: the token travels in a hidden form field this page renders, so nothing needs to
    # read the cookie from JavaScript. A page that later posts from a bare button rather than a
    # form would need `hx-headers` and therefore a readable cookie -- a deliberate change, not a
    # default to leave open in the meantime.
    response.set_cookie(CSRF_COOKIE, token.value, httponly=True, samesite='lax', max_age=SESSION_MAX_AGE_SECONDS)
    return response


@router.post('/sections/{section_id}/grades')
async def record_grade(
    section_id: str,
    request: Request,
    grades: Grades,
    actor: CurrentActor,
    templates: PageTemplates,
    student_id: Annotated[str, Form()],
    grade: Annotated[int, Form()],
) -> Response:
    """Record one grade and give back the row it changed (UC-22).

    The response is the student's row as it now stands, which is what makes the standing
    trustworthy: recording a 4 after an earlier 7 changes nothing about whether the subject is
    passed, and the row that comes back says so rather than showing the 4 as though it were the
    answer.

    The row is taken from a fresh listing rather than assembled from
    :class:`~academy.application.dtos.GradeRecordedDto`, which carries the standing but not the
    student's name or attempt count. Two reads in one transaction, and the row displayed is
    provably the row stored. Widening the DTO instead would be a use-case change made to suit one
    template, which is the direction this architecture exists to refuse.
    """
    recorded = await grades.record_grade(
        RecordGradeCommand(actor=actor, section_id=section_id, student_id=student_id, grade=grade)
    )
    sheet = await grades.list_section_grades(ListSectionGradesCommand(actor=actor, section_id=section_id))

    # The token is carried through because the row that comes back contains the form for the
    # *next* grade on that student. A fragment that dropped it would swap a form nobody can
    # submit into the page, and only the second attempt would fail.
    return templates.fragment(
        '_grade_row.html',
        {
            'row': _row_for(sheet, recorded.student_id),
            'section_id': section_id,
            'csrf_token': token_for(request).value,
            'just_recorded': True,
        },
    )


def _row_for(sheet: SectionGradesDto, student_id: str) -> SectionGradeRowDto:
    """Find the row the grade was just recorded against.

    Raises:
        LookupError: If the student is not on the sheet, which cannot happen -- the use case has
            already refused a grade for an unenrolled student -- and is therefore a bug of ours
            rather than something to render. It escapes as a 500 with its traceback, which is the
            same decision the CLI makes for anything the table does not classify.
    """
    for row in sheet.rows:
        if row.student_id == student_id:
            return row
    raise LookupError(f'{student_id} was graded in this section but is not on its sheet')
