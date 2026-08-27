"""Pipeline d'alertes : dédup Redis (TTL 72h) + cooldown par route + email Gmail.

Statuts email possibles sur la ligne `alerts` :
- sent            : email parti
- failed          : échec SMTP après retries (visible dans l'admin)
- skipped_config  : alerte CACHE_SEULEMENT et send_cache_only_alerts=False
- skipped_no_smtp : GMAIL_SMTP_USER / GMAIL_APP_PASSWORD non configurés
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.metrics import ALERTS_EMAIL
from app.models import Alert
from app.redis_client import get_redis
from app.schemas import WatchConfig
from app.services.detector import Anomaly
from app.services.duffel import DuffelUnavailable, confirm_anomaly_live, duffel_enabled
from app.services.mailer import send_email, smtp_configured

logger = logging.getLogger(__name__)

CONFIDENCE_LIVE = "CONFIRME_LIVE"
CONFIDENCE_CACHE = "CACHE_SEULEMENT"

TYPE_LABELS = {
    "seuil": "sous le seuil vs médiane 30j",
    "record": "nouveau record absolu",
    "chute": "chute brutale vs relevé précédent",
    "cross_devise": "différentiel cross-devise vs devise principale",
}

_email_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent.parent / "web" / "templates" / "email"),
    autoescape=select_autoescape(["html"]),
)


def _dedup_key(anomaly: Anomaly) -> str:
    r = anomaly.route
    return f"alert:{r.origin}{r.destination}:{anomaly.currency}:{round(anomaly.price)}:{anomaly.primary_type}"


def _cooldown_key(anomaly: Anomaly) -> str:
    r = anomaly.route
    return f"cooldown:{r.origin}{r.destination}:{anomaly.currency}"


async def _passes_antispam(anomaly: Anomaly) -> bool:
    """Dédup 72h sur (route + prix arrondi + type) et cooldown 2h par route,
    contourné si le nouveau prix est encore plus bas. Fail-open si Redis est down :
    mieux vaut un doublon qu'une error fare ratée."""
    settings = get_settings()
    redis = get_redis()
    try:
        if await redis.exists(_dedup_key(anomaly)):
            logger.info("Alerte dédupliquée", extra={"extra_fields": {"key": _dedup_key(anomaly)}})
            return False
        cooldown_price = await redis.get(_cooldown_key(anomaly))
        if cooldown_price is not None and anomaly.price >= float(cooldown_price):
            logger.info(
                "Alerte en cooldown",
                extra={"extra_fields": {"key": _cooldown_key(anomaly), "prev_price": cooldown_price}},
            )
            return False
        await redis.set(_dedup_key(anomaly), "1", ex=settings.dedup_ttl_hours * 3600)
        await redis.set(_cooldown_key(anomaly), str(anomaly.price), ex=settings.cooldown_hours * 3600)
        return True
    except Exception:
        logger.warning("Redis indisponible, envoi sans dédup", exc_info=True)
        return True


async def _release_antispam(anomaly: Anomaly) -> None:
    """Libère la réservation si aucun email n'est parti afin que le cycle
    suivant puisse retenter une confirmation live ou un SMTP momentanément HS."""
    try:
        await get_redis().delete(_dedup_key(anomaly), _cooldown_key(anomaly))
    except Exception:
        logger.warning("Redis indisponible pendant la libération anti-spam", exc_info=True)


def _links(anomaly: Anomaly) -> dict:
    r = anomaly.route
    query = f"flights from {r.origin} to {r.destination}"
    if anomaly.depart_date:
        query += f" on {anomaly.depart_date}"
    return {
        "google_flights": f"https://www.google.com/travel/flights?q={quote(query)}",
        "aviasales": f"https://www.aviasales.com{anomaly.link}" if anomaly.link else None,
    }


def build_email(
    anomaly: Anomaly,
    confidence: str,
    live_price: float | None = None,
    live_currency: str | None = None,
) -> tuple[str, str, str]:
    """Retourne (sujet, html, texte)."""
    r = anomaly.route
    pct = anomaly.pct_below_median
    icon = "🔥" if confidence == CONFIDENCE_LIVE else "📉"
    kind = "Error fare probable" if confidence == CONFIDENCE_LIVE else "Prix bas détecté"
    subject = f"{icon} {kind} {anomaly.currency} {anomaly.price:.0f} — {r.origin} → {r.destination}"
    if pct is not None:
        subject += f" (-{pct:.0f} % vs médiane)"

    freshness_min = None
    if anomaly.fetched_at is not None:
        freshness_min = int((datetime.now(timezone.utc) - anomaly.fetched_at).total_seconds() // 60)

    context = {
        "anomaly": anomaly,
        "route": r,
        "confidence": confidence,
        "confidence_label": (
            "🔥 Confirmée live (Duffel)" if confidence == CONFIDENCE_LIVE
            else "📉 Cache Travelpayouts (~48h de latence possible), non confirmée live"
        ),
        "pct": pct,
        "types_labels": [TYPE_LABELS.get(t, t) for t in anomaly.types],
        "links": _links(anomaly),
        "freshness_min": freshness_min,
        "live_price": live_price,
        "live_currency": live_currency or anomaly.currency,
    }
    html = _email_env.get_template("alert.html").render(**context)
    text = _email_env.get_template("alert.txt").render(**context)
    return subject, html, text


async def process_anomalies(
    session: AsyncSession, anomalies: list[Anomaly], config: WatchConfig
) -> dict:
    """Applique l'anti-spam, journalise chaque alerte en DB et envoie les emails."""
    settings = get_settings()
    summary = {"sent": 0, "failed": 0, "skipped": 0, "deduped": 0}

    for anomaly in anomalies:
        if not await _passes_antispam(anomaly):
            summary["deduped"] += 1
            continue

        # Étage 2 : confirmation live Duffel (jamais bloquante pour le pipeline)
        confidence = CONFIDENCE_CACHE
        live_price: float | None = None
        live_currency: str | None = None
        if duffel_enabled():
            try:
                confirmed, live_price, live_currency = await confirm_anomaly_live(
                    anomaly.route.origin,
                    anomaly.route.destination,
                    anomaly.depart_date,
                    anomaly.return_date,
                    anomaly.price,
                    anomaly.currency,
                )
                if confirmed:
                    confidence = CONFIDENCE_LIVE
            except DuffelUnavailable as exc:
                logger.warning(
                    "Confirmation live indisponible",
                    extra={"extra_fields": {"reason": str(exc)}},
                )

        now = datetime.now(timezone.utc)
        alert = Alert(
            route_id=anomaly.route.id,
            type=anomaly.primary_type,
            currency=anomaly.currency,
            price=anomaly.price,
            confidence=confidence,
            dedup_key=f"{_dedup_key(anomaly)}:{int(now.timestamp())}",
            email_status="pending",
        )
        session.add(alert)
        await session.flush()

        if confidence == CONFIDENCE_CACHE and not config.send_cache_only_alerts:
            alert.email_status = "skipped_config"
            summary["skipped"] += 1
        elif not smtp_configured():
            alert.email_status = "skipped_no_smtp"
            summary["skipped"] += 1
            logger.warning("SMTP non configuré, alerte journalisée sans email")
        else:
            to = config.alert_email_to or settings.alert_email_to
            subject, html, text = build_email(anomaly, confidence, live_price, live_currency)
            try:
                await send_email(to, subject, html, text)
                alert.email_status = "sent"
                alert.sent_at = datetime.now(timezone.utc)
                summary["sent"] += 1
            except Exception:
                alert.email_status = "failed"
                summary["failed"] += 1
                logger.exception(
                    "Échec d'envoi de l'alerte email",
                    extra={"extra_fields": {"to": to, "subject": subject}},
                )
        await session.commit()
        ALERTS_EMAIL.labels(status=alert.email_status).inc()
        if alert.email_status != "sent":
            await _release_antispam(anomaly)

    return summary
