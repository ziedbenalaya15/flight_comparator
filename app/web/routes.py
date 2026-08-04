from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE_NAME, extract_token, require_admin
from app.config import get_settings
from app.db import get_session
from app.models import PriceSnapshot, Route
from app.services.config_service import get_active_config
from app.state import collection_state

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

TOP_PER_ROUTE = 3


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    # Pas de token valide -> mini formulaire de connexion, pas d'erreur brute
    if extract_token(request) != get_settings().admin_token:
        return templates.TemplateResponse(request, "login.html", status_code=401)

    config = await get_active_config(session)
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    rows = (
        await session.execute(
            select(PriceSnapshot, Route)
            .join(Route, PriceSnapshot.route_id == Route.id)
            .where(PriceSnapshot.fetched_at >= since)
            .order_by(PriceSnapshot.price.asc())
            .limit(2000)
        )
    ).all()

    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for snapshot, route in rows:
        key = (route.origin, route.destination, snapshot.currency)
        bucket = grouped[key]
        if len(bucket) < TOP_PER_ROUTE:
            bucket.append(
                {
                    "price": snapshot.price,
                    "currency": snapshot.currency,
                    "airline": snapshot.airline,
                    "transfers": snapshot.transfers,
                    "depart_date": snapshot.depart_date,
                    "return_date": snapshot.return_date,
                    "fetched_at": snapshot.fetched_at,
                    "link": f"https://www.aviasales.com{snapshot.link}" if snapshot.link else None,
                }
            )

    routes_view = [
        {"origin": o, "destination": d, "currency": c, "offers": offers}
        for (o, d, c), offers in sorted(grouped.items())
    ]

    total_snapshots = await session.scalar(select(func.count(PriceSnapshot.id)))

    response = templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "config": config,
            "routes_view": routes_view,
            "total_snapshots": total_snapshots,
            "job": collection_state,
            "now": datetime.now(timezone.utc),
        },
    )
    # Le token arrive en query param la 1ère fois -> on le pose en cookie de session
    if request.query_params.get("token"):
        response.set_cookie(COOKIE_NAME, get_settings().admin_token, httponly=True, samesite="lax")
    return response
