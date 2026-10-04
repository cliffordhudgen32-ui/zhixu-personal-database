from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field

from app.schemas import InputBase
from app.services import knowledge_answer

router = APIRouter()


class AnswerRequest(InputBase):
    question: str = Field(min_length=1, max_length=1000)
    start: date | None = None
    end: date | None = None
    project: UUID | None = None


@router.post('/api/knowledge/answer')
def answer(payload: AnswerRequest):
    if not payload.question.strip():
        raise HTTPException(400, '请输入你的问题')
    if payload.start and payload.end and payload.start > payload.end:
        raise HTTPException(400, '起始日期不能晚于结束日期')
    filters = {key: str(value) for key, value in payload.model_dump(exclude={'question'}, exclude_none=True).items()}
    return knowledge_answer.answer(payload.question.strip(), filters)
