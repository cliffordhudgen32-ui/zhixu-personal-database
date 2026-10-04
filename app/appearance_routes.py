"""Local desktop and browser share visual preferences stored on the data drive."""
from fastapi import APIRouter, HTTPException
from app.services.appearance import read_preferences, save_preferences

router = APIRouter()


@router.get('/api/appearance')
def appearance_preferences():
    return read_preferences()


@router.put('/api/appearance')
def appearance_update(payload: dict):
    try:
        return save_preferences(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, '皮肤偏好未保存，请检查数据目录的写入权限') from exc
