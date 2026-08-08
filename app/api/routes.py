import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, PriceSnapshot, Route

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
    return {
        "config": config.model_dump(mode="json"),
        "expanded_origins": config.expanded_origins,
        "expanded_destinations": config.expanded_destinations,
    }


@router.put("/api/config", dependencies=[Depends(require_admin)])
async def write_config(config: WatchConfig, session: AsyncSession = Depends(get_session)) -> dict:
    """Remplace la configuration active (nouvelle version, l'ancienne est archivée)."""
    await save_config(session, config)
    return {
        "status": "saved",
        "expanded_origins": config.expanded_origins,
        "expanded_destinations": config.expanded_destinations,
    }


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


@router.post("/api/premium", dependencies=[Depends(require_admin)])
async def premium(session: AsyncSession = Depends(get_session)) -> dict:
    """Interrogation Duffel à la demande pour les cabines avant (équivalent /premium)."""
    from app.services.duffel import duffel_enabled
    from app.services.premium import premium_check

    if not duffel_enabled():
        return {"status": "disabled", "detail": "DUFFEL_TOKEN non configuré", "results": []}
    config = await get_active_config(session)
    if config is None:
        raise HTTPException(status_code=404, detail="aucune configuration active")
    return await premium_check(session, config)


@router.get("/api/export", dependencies=[Depends(require_admin)])
async def export_csv(
    dataset: str = "snapshots",
    days: int = 30,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export CSV de l'historique (`dataset=snapshots` ou `dataset=alerts`)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if dataset == "snapshots":
        writer.writerow(
            ["fetched_at", "origin", "destination", "currency", "price", "source",
             "cabin", "transfers", "return_transfers", "airline", "depart_date", "return_date"]
        )
        rows = await session.stream(
            select(PriceSnapshot, Route)
            .join(Route, PriceSnapshot.route_id == Route.id)
            .where(PriceSnapshot.fetched_at >= since)
            .order_by(PriceSnapshot.fetched_at)
        )
        async for snapshot, route in rows:
            writer.writerow(
                [snapshot.fetched_at.isoformat(), route.origin, route.destination,
                 snapshot.currency, snapshot.price, snapshot.source, snapshot.cabin,
                 snapshot.transfers, snapshot.return_transfers, snapshot.airline,
                 snapshot.depart_date, snapshot.return_date]
            )
    elif dataset == "alerts":
        writer.writerow(
            ["created_at", "origin", "destination", "type", "currency", "price",
             "confidence", "email_status", "sent_at"]
        )
        rows = await session.stream(
            select(Alert, Route)
            .join(Route, Alert.route_id == Route.id)
            .where(Alert.created_at >= since)
            .order_by(Alert.created_at)
        )
        async for alert, route in rows:
            writer.writerow(
                [alert.created_at.isoformat(), route.origin, route.destination,
                 alert.type, alert.currency, alert.price, alert.confidence,
                 alert.email_status, alert.sent_at.isoformat() if alert.sent_at else ""]
            )
    else:
        raise HTTPException(status_code=400, detail="dataset doit être 'snapshots' ou 'alerts'")

    filename = f"{dataset}_{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/digest/send", dependencies=[Depends(require_admin)])
async def trigger_digest() -> dict:
    """Déclenche l'envoi du digest immédiatement (test/manuel)."""
    from app.services.digest import send_daily_digest

    return await send_daily_digest()


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
