"""Reading a transcript, and listing the wards in somebody's care.

Two routes over one driving port, and between them they answer UC-26, UC-28 and UC-30. They are
the second pair written against the shape ADR-0021 settled, and they add nothing to it: a
dependency per port, no ``try`` anywhere, and the error boundary renders whatever the use case
raises.

The wards page is worth reading for what it does *not* do. A guardianship ends when the ward comes
of age, and nothing is written when that happens -- no job runs and no record changes. The list is
derived on every read from each ward's age against the global age of majority, so a ward who had a
birthday overnight is simply absent the next morning. This route does none of that work and could
not: it asks the use case and renders the answer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from academy.adapters.inbound.web.csrf import enforce, token_for
from academy.adapters.inbound.web.dependencies import CurrentActor, PageTemplates, Records, web_surface
from academy.application.commands import ListMyWardsCommand, ViewAcademicHistoryCommand

router = APIRouter(dependencies=[Depends(web_surface), Depends(enforce)], tags=['records'])


@router.get('/wards')
async def wards(
    request: Request,
    records: Records,
    actor: CurrentActor,
    templates: PageTemplates,
) -> Response:
    """List the students currently in the actor's care (UC-28).

    There is no student id in the path, and that is the point. The command carries the *actor*
    rather than a person id, so an inbound adapter cannot pass one out of the request -- which
    would hand anyone the ability to enumerate anyone else's wards. The subject of the question
    and the person asking it are the same by construction.
    """
    in_care = await records.list_my_wards(ListMyWardsCommand(actor=actor))

    return templates.page('wards.html', {'wards': in_care, 'csrf_token': token_for(request).value})


@router.get('/students/{student_id}/transcript')
async def transcript(
    student_id: str,
    request: Request,
    records: Records,
    actor: CurrentActor,
    templates: PageTemplates,
) -> Response:
    """Show a student's full transcript and per-subject standing (UC-26, UC-30).

    One route for the student reading their own record and the guardian reading a ward's, because
    they ask the identical question and differ only in the relation that authorizes it. A guardian
    whose ward has come of age is refused here with nothing having changed in storage.
    """
    history = await records.view_academic_history(ViewAcademicHistoryCommand(actor=actor, student_id=student_id))

    return templates.page('transcript.html', {'history': history, 'csrf_token': token_for(request).value})
