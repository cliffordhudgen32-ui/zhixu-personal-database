from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.services.extraction import extract_attachment, get_extraction

router = APIRouter()


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    force: bool = False


@router.get('/api/attachments/{uid}/extraction')
def attachment_text(uid: str):
    return get_extraction(uid)


@router.post('/api/attachments/{uid}/extraction')
@router.post('/api/attachments/{uid}/extract')
def extract(uid: str, payload: ExtractionRequest | None = None):
    return extract_attachment(uid, force=payload.force if payload else False)
