from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi.responses import RedirectResponse

from app.auth import COOKIE_NAME, extract_token
from app.config import get_settings
from app.db import get_session
from app.models import Alert, PriceSnapshot, Route
from app.schemas import CABIN_LABELS, CABINS, WatchConfig, parse_window
from app.services.config_service import get_active_config, save_config
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

    economy_budget = config.budgets.get("economy") if config else None
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for snapshot, route in rows:
        key = (route.origin, route.destination, snapshot.currency)
        bucket = grouped[key]
        if len(bucket) < TOP_PER_ROUTE:
            over_budget = (
                economy_budget is not None
                and config is not None
                and snapshot.currency == config.main_currency
                and float(snapshot.price) > economy_budget
            )
            bucket.append(
                {
                    "over_budget": over_budget,
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

    recent_alerts = (
        await session.execute(
            select(Alert, Route)
            .join(Route, Alert.route_id == Route.id)
            .order_by(Alert.created_at.desc())
            .limit(10)
        )
    ).all()

    response = templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "config": config,
            "routes_view": routes_view,
            "recent_alerts": recent_alerts,
            "total_snapshots": total_snapshots,
            "job": collection_state,
            "now": datetime.now(timezone.utc),
        },
    )
    # Le token arrive en query param la 1ère fois -> on le pose en cookie de session
    if request.query_params.get("token"):
        response.set_cookie(COOKIE_NAME, get_settings().admin_token, httponly=True, samesite="lax")
    return response


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _opt_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def _config_from_form(form) -> WatchConfig:
    budgets = {}
    for cabin in CABINS:
        raw = (form.get(f"budget_{cabin}") or "").strip().replace(",", ".")
        if raw:
            budgets[cabin] = float(raw)
    return_window_raw = (form.get("return_window") or "").strip()
    return WatchConfig(
        destinations=_csv(form.get("destinations") or ""),
        origins=_csv(form.get("origins") or ""),
        depart_window=parse_window(form.get("depart_window") or ""),
        return_window=parse_window(return_window_raw) if return_window_raw else None,
        stay_nights_min=_opt_int(form.get("stay_nights_min") or ""),
        stay_nights_max=_opt_int(form.get("stay_nights_max") or ""),
        cabins=form.getlist("cabins"),
        max_stopovers=_opt_int(form.get("max_stopovers") or ""),
        budgets=budgets,
        currencies=_csv(form.get("currencies") or ""),
        threshold_pct=float((form.get("threshold_pct") or "40").replace(",", ".")),
        alert_email_to=(form.get("alert_email_to") or "").strip() or None,
        send_cache_only_alerts="send_cache_only_alerts" in form,
        digest_enabled="digest_enabled" in form,
        paused="paused" in form,
    )


def _render_config_page(request: Request, config: WatchConfig, saved: str | None, error: str | None):
    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "config": config,
            "all_cabins": CABINS,
            "cabin_labels": CABIN_LABELS,
            "saved": saved,
            "error": error,
        },
    )


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, session: AsyncSession = Depends(get_session)):
    if extract_token(request) != get_settings().admin_token:
        return templates.TemplateResponse(request, "login.html", status_code=401)
    config = await get_active_config(session)
    return _render_config_page(request, config, request.query_params.get("saved"), None)


@router.post("/config", response_class=HTMLResponse)
async def config_save(request: Request, session: AsyncSession = Depends(get_session)):
    if extract_token(request) != get_settings().admin_token:
        return templates.TemplateResponse(request, "login.html", status_code=401)
    form = await request.form()
    try:
        config = _config_from_form(form)
    except (ValueError, TypeError) as exc:
        current = await get_active_config(session)
        return _render_config_page(request, current, None, str(exc))
    version = await save_config(session, config)
    return RedirectResponse(url=f"/config?saved={version}", status_code=303)
