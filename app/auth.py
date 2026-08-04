from fastapi import HTTPException, Request

from app.config import get_settings

COOKIE_NAME = "admin_token"


def extract_token(request: Request) -> str | None:
    return (
        request.query_params.get("token")
        or request.headers.get("X-Admin-Token")
        or request.cookies.get(COOKIE_NAME)
    )


def require_admin(request: Request) -> str:
    token = extract_token(request)
    if token != get_settings().admin_token:
        raise HTTPException(status_code=401, detail="Token admin invalide ou manquant")
    return token
