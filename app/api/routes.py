import asyncio
import csv
import io
import logging
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
logger = logging.getLogger(__name__)


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


@router.post("/api/offers/{snapshot_id}/verify", dependencies=[Depends(require_admin)])
async def verify_offer_live(snapshot_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Revérifie une offre Travelpayouts précise auprès de Duffel.

    Travelpayouts reste la source de découverte en cache ; ce contrôle ciblé
    utilise exactement la route et les dates de la ligne cliquée.
    """
    from app.services.duffel import DuffelUnavailable, duffel_enabled, search_offers

    if not duffel_enabled():
        return {"status": "disabled", "detail": "DUFFEL_TOKEN non configuré", "offers": []}
    row = (
        await session.execute(
            select(PriceSnapshot, Route)
            .join(Route, PriceSnapshot.route_id == Route.id)
            .where(PriceSnapshot.id == snapshot_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="offre introuvable")
    snapshot, route = row
    if snapshot.depart_date is None:
        raise HTTPException(status_code=422, detail="date de départ absente, vérification impossible")
    try:
        offers = await search_offers(
            route.origin,
            route.destination,
            snapshot.depart_date,
            snapshot.return_date,
            cabin="economy",
        )
    except DuffelUnavailable as exc:
        return {"status": "unavailable", "detail": str(exc), "offers": []}

    return {
        "status": "ok",
        "origin": route.origin,
        "destination": route.destination,
        "depart_date": snapshot.depart_date.isoformat(),
        "return_date": snapshot.return_date.isoformat() if snapshot.return_date else None,
        "cached_price": float(snapshot.price),
        "cached_currency": snapshot.currency,
        "cached_at": snapshot.fetched_at.isoformat(),
        "live_mode": offers[0].live_mode if offers else None,
        "offers": [
            {
                "price": offer.price,
                "currency": offer.currency,
                "airline": offer.airline,
                "segments_out": offer.segments_out,
                "segments_back": offer.segments_back,
                "expires_at": offer.expires_at,
            }
            for offer in offers
        ],
    }


@router.get("/api/notifications/status", dependencies=[Depends(require_admin)])
async def notification_status(session: AsyncSession = Depends(get_session)) -> dict:
    """État non sensible de la chaîne d'alerte et dernier résultat connu."""
    from app.config import get_settings
    from app.services.duffel import duffel_enabled
    from app.services.mailer import email_configured, email_provider, smtp_configured

    config = await get_active_config(session)
    latest = await session.scalar(select(Alert).order_by(Alert.created_at.desc()).limit(1))
    settings = get_settings()
    return {
        "email_configured": email_configured(),
        "email_provider": email_provider(),
        "smtp_configured": smtp_configured(),
        "recipient_configured": bool((config.alert_email_to if config else None) or settings.alert_email_to),
        "duffel_configured": duffel_enabled(),
        "send_cache_only_alerts": config.send_cache_only_alerts if config else None,
        "interval_minutes": settings.collect_interval_minutes,
        "last_alert": (
            {
                "created_at": latest.created_at.isoformat(),
                "confidence": latest.confidence,
                "email_status": latest.email_status,
                "sent_at": latest.sent_at.isoformat() if latest.sent_at else None,
            }
            if latest
            else None
        ),
    }


@router.post("/api/notifications/test", dependencies=[Depends(require_admin)])
async def test_notification(session: AsyncSession = Depends(get_session)) -> dict:
    """Envoie un email de test explicite au destinataire configuré."""
    from app.config import get_settings
    from app.services.mailer import EmailDeliveryError, email_configured, email_provider, send_email

    if not email_configured():
        return {"status": "disabled", "detail": "Aucun fournisseur email configuré"}
    config = await get_active_config(session)
    settings = get_settings()
    recipient = (config.alert_email_to if config else None) or settings.alert_email_to
    if not recipient:
        return {"status": "disabled", "detail": "destinataire d'alerte non configuré"}
    now = datetime.now(timezone.utc)
    provider = email_provider()
    provider_label = "Resend HTTPS" if provider == "resend" else "Gmail SMTP"
    try:
        await send_email(
            recipient,
            "✅ Test Flight Error Fare Watcher",
            (
                "<h2>✅ Notifications opérationnelles</h2>"
                f"<p>Railway a réussi à envoyer cet email via {provider_label}.</p>"
                f"<p>Test UTC : {now:%Y-%m-%d %H:%M:%S}</p>"
            ),
            f"Notifications opérationnelles via {provider_label}. "
            f"Test Railway UTC : {now:%Y-%m-%d %H:%M:%S}",
        )
    except EmailDeliveryError as exc:
        logger.warning(
            "Échec du test de notification",
            extra={"extra_fields": {"provider": provider, "reason": str(exc)}},
        )
        return {"status": "failed", "detail": str(exc), "provider": provider}
    except Exception:
        logger.exception(
            "Échec inattendu du test de notification",
            extra={"extra_fields": {"provider": provider}},
        )
        return {
            "status": "failed",
            "detail": "Échec inattendu de l'envoi ; consulter les logs Railway",
            "provider": provider,
        }
    return {"status": "sent", "sent_at": now.isoformat(), "provider": provider}


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
