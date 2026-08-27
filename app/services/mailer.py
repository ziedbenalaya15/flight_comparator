"""Envoi d'emails via Resend HTTPS, avec Gmail SMTP comme solution de repli."""
import hashlib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Erreur d'envoi formulée sans exposer de secret du fournisseur."""


class _TransientResendError(RuntimeError):
    pass


def smtp_configured() -> bool:
    """Compatibilité : indique uniquement si le secours Gmail SMTP est prêt."""
    s = get_settings()
    return bool(s.gmail_smtp_user and s.gmail_app_password)


def email_provider() -> str | None:
    settings = get_settings()
    if settings.resend_api_key:
        return "resend"
    if settings.gmail_smtp_user and settings.gmail_app_password:
        return "gmail_smtp"
    return None


def email_configured() -> bool:
    return email_provider() is not None


def _resend_idempotency_key(to: str, subject: str, text_body: str) -> str:
    digest = hashlib.sha256(f"{to}\0{subject}\0{text_body}".encode()).hexdigest()
    return f"flight-alert-{digest[:32]}"


@retry(
    retry=retry_if_exception_type((httpx.TransportError, _TransientResendError)),
    wait=wait_exponential(multiplier=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _send_resend(to: str, subject: str, html_body: str, text_body: str) -> None:
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Idempotency-Key": _resend_idempotency_key(to, subject, text_body),
    }
    payload = {
        "from": settings.resend_from_email,
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://api.resend.com/emails", headers=headers, json=payload)
    if response.status_code == 429 or response.status_code >= 500:
        raise _TransientResendError(f"HTTP {response.status_code}")
    if not response.is_success:
        raise EmailDeliveryError(f"Resend a refusé l'envoi (HTTP {response.status_code})")


@retry(
    retry=retry_if_exception_type((aiosmtplib.SMTPException, ConnectionError, TimeoutError, OSError)),
    wait=wait_exponential(multiplier=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def _send_smtp(to: str, subject: str, html_body: str, text_body: str) -> None:
    settings = get_settings()
    message = MIMEMultipart("alternative")
    message["From"] = settings.gmail_smtp_user
    message["To"] = to
    message["Subject"] = subject
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    await aiosmtplib.send(
        message,
        hostname="smtp.gmail.com",
        port=587,
        start_tls=True,
        username=settings.gmail_smtp_user,
        password=settings.gmail_app_password,
        timeout=15,
    )


async def send_email(to: str, subject: str, html_body: str, text_body: str) -> str:
    """Envoie un message avec Resend en priorité et retourne le fournisseur utilisé."""
    if not to:
        raise EmailDeliveryError("Destinataire email non configuré")

    provider = email_provider()
    if provider is None:
        raise EmailDeliveryError("Aucun fournisseur email configuré")

    try:
        if provider == "resend":
            await _send_resend(to, subject, html_body, text_body)
        else:
            await _send_smtp(to, subject, html_body, text_body)
    except EmailDeliveryError:
        raise
    except (httpx.TransportError, _TransientResendError) as exc:
        raise EmailDeliveryError("Connexion à Resend impossible après 3 tentatives") from exc
    except (aiosmtplib.SMTPException, ConnectionError, TimeoutError, OSError) as exc:
        raise EmailDeliveryError("Connexion Gmail SMTP impossible après 3 tentatives") from exc

    logger.info(
        "Email envoyé",
        extra={"extra_fields": {"to": to, "subject": subject, "provider": provider}},
    )
    return provider
