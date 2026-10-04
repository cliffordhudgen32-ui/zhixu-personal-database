"""Local text capture drafts require explicit confirmation before becoming entries."""
from uuid import UUID

from fastapi import APIRouter, Query

from app.services import capture
from app.services.capture import CaptureInput, DraftStatus, RevisionInput

router = APIRouter()


@router.get('/api/capture-drafts')
def capture_list(status: DraftStatus = 'open', page: int = Query(1, ge=1, le=100_000),
                 size: int = Query(20, ge=1, le=50)):
    return capture.list_drafts(status, page, size)


@router.get('/api/capture-drafts/{uid}')
def capture_detail(uid: UUID):
    return capture.get_draft(uid)


@router.put('/api/capture-drafts/{uid}')
def capture_save(uid: UUID, payload: CaptureInput):
    return capture.save_draft(uid, payload)


@router.post('/api/capture-drafts/{uid}/submit')
def capture_submit(uid: UUID, payload: RevisionInput):
    return capture.submit_draft(uid, payload.revision)


@router.post('/api/capture-drafts/{uid}/discard')
def capture_discard(uid: UUID, payload: RevisionInput):
    return capture.discard_draft(uid, payload.revision)


@router.post('/api/capture-drafts/{uid}/restore')
def capture_restore(uid: UUID, payload: RevisionInput):
    return capture.restore_draft(uid, payload.revision)
