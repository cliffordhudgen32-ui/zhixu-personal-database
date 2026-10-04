from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from app.services import speech

router = APIRouter()


class SpeechPrepare(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: Literal['base', 'small'] = 'base'


@router.get('/api/speech/status')
def speech_status():
    return speech.status()


@router.post('/api/speech/prepare')
def speech_prepare(payload: SpeechPrepare):
    return speech.prepare(payload.model)


@router.post('/api/speech/transcribe')
async def speech_transcribe(file: UploadFile = File(...), model: str = Form('base'), language: str = Form('zh')):
    chunks = []
    size = 0
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > speech.MAX_BYTES:
                raise HTTPException(413, '录音文件不能超过 25 MB，请分段处理')
            chunks.append(chunk)
        return await run_in_threadpool(speech.transcribe, b''.join(chunks), file.filename or '', model, language)
    finally:
        await file.close()
