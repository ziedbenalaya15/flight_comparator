"""Digest quotidien (8h Europe/Paris) : meilleurs prix des 24 dernières heures
par destination, envoyé même sans alerte si activé dans la config."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionFactory
from app.models import PriceSnapshot, Route
from app.services.config_service import get_active_config
from app.services.mailer import send_email, smtp_configured

logger = logging.getLogger(__name__)

TOP_PER_DESTINATION = 3

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent.parent / "web" / "templates" / "email"),
    autoescape=select_autoescape(["html"]),
)


async def send_daily_digest() -> dict:
    settings = get_settings()
    async with SessionFactory() as session:
        config = await get_active_config(session)
        if config is None or not config.digest_enabled:
            return {"status": "disabled"}
        if not smtp_configured():
            logger.warning("Digest activé mais SMTP non configuré")
            return {"status": "no_smtp"}

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = (
            await session.execute(
                select(PriceSnapshot, Route)
                .join(Route, PriceSnapshot.route_id == Route.id)
                .where(
                    PriceSnapshot.fetched_at >= since,
                    PriceSnapshot.currency == config.main_currency,
                    PriceSnapshot.cabin == "economy",
                )
                .order_by(PriceSnapshot.price.asc())
                .limit(3000)
            )
        ).all()

    by_destination: dict[str, list[dict]] = {}
    for snapshot, route in rows:
        bucket = by_destination.setdefault(route.destination, [])
        if len(bucket) < TOP_PER_DESTINATION:
            budget = config.budgets.get("economy")
            bucket.append(
                {
                    "origin": route.origin,
                    "price": float(snapshot.price),
                    "currency": snapshot.currency,
                    "airline": snapshot.airline,
                    "depart_date": snapshot.depart_date,
                    "return_date": snapshot.return_date,
                    "over_budget": bool(budget and float(snapshot.price) > budget),
                }
            )

    context = {
        "by_destination": dict(sorted(by_destination.items())),
        "main_currency": config.main_currency,
        "date_label": datetime.now(timezone.utc).strftime("%d/%m/%Y"),
    }
    subject = f"🗞️ Digest vols du {context['date_label']} — meilleurs prix 24h"
    html = _env.get_template("digest.html").render(**context)
    text = _env.get_template("digest.txt").render(**context)
    to = config.alert_email_to or settings.alert_email_to

    try:
        await send_email(to, subject, html, text)
    except Exception:
        logger.exception("Échec d'envoi du digest")
        return {"status": "failed"}
    return {"status": "sent", "destinations": len(by_destination)}
