import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.services.collector import run_collection
from app.state import collection_state

router = APIRouter()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/api/check", dependencies=[Depends(require_admin)])
async def check_now() -> dict:
    """Déclenche une collecte immédiate (équivalent /check). Répond tout de suite,
    la collecte tourne en tâche de fond."""
    if collection_state.running:
        return {"status": "already_running"}
    asyncio.get_running_loop().create_task(run_collection(trigger="manual"))
    return {"status": "started"}


@router.get("/api/status", dependencies=[Depends(require_admin)])
async def status() -> dict:
    return {
        "running": collection_state.running,
        "last_run_at": collection_state.last_run_at,
        "last_status": collection_state.last_status,
        "last_detail": collection_state.last_detail,
        "last_snapshot_count": collection_state.last_snapshot_count,
        "last_trigger": collection_state.last_trigger,
    }
