"""Manual task creation from explicit follow-up sections of AI review drafts."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from app.schemas import InputBase
from app.services import action_followup

router = APIRouter()


class FollowupTaskRequest(InputBase):
    revision: int = Field(ge=1)
    content_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    title: str = Field(min_length=1, max_length=500)
    due_date: date | None = None
    source_uuid: UUID | None = None
    project_uuid: UUID | None = None
    confirmation: str


@router.get('/api/ai/drafts/{uid}/followups')
def followup_options(uid: UUID):
    return action_followup.candidate_options(str(uid))


@router.post('/api/ai/drafts/{uid}/followups/{candidate_id}/task')
def followup_create(uid: UUID, candidate_id: str, payload: FollowupTaskRequest):
    from fastapi import HTTPException
    import re
    if not re.fullmatch(r'[a-f0-9]{64}', candidate_id):
        raise HTTPException(400, '后续事项标识无效')
    return action_followup.create_task(str(uid), candidate_id, payload)
