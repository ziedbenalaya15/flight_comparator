import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.schemas import WatchConfig
from app.services.collector import run_collection
from app.services.config_service import get_active_config, save_config
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


@router.get("/api/config", dependencies=[Depends(require_admin)])
async def read_config(session: AsyncSession = Depends(get_session)) -> dict:
    config = await get_active_config(session)
    if config is None:
        raise HTTPException(status_code=404, detail="aucune configuration active")
    return {"config": config.model_dump(mode="json"), "expanded_origins": config.expanded_origins}


@router.put("/api/config", dependencies=[Depends(require_admin)])
async def write_config(config: WatchConfig, session: AsyncSession = Depends(get_session)) -> dict:
    """Remplace la configuration active (nouvelle version, l'ancienne est archivée)."""
    await save_config(session, config)
    return {"status": "saved", "expanded_origins": config.expanded_origins}


@router.post("/api/config/pause", dependencies=[Depends(require_admin)])
async def pause(session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_paused(session, True)


@router.post("/api/config/resume", dependencies=[Depends(require_admin)])
async def resume(session: AsyncSession = Depends(get_session)) -> dict:
    return await _set_paused(session, False)


async def _set_paused(session: AsyncSession, paused: bool) -> dict:
    config = await get_active_config(session)
    if config is None:
        raise HTTPException(status_code=404, detail="aucune configuration active")
    config.paused = paused
    await save_config(session, config)
    return {"status": "saved", "paused": paused}


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
