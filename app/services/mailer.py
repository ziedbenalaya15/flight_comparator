"""Envoi d'emails via Gmail SMTP (STARTTLS + mot de passe d'application)."""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    s = get_settings()
    return bool(s.gmail_smtp_user and s.gmail_app_password)


@retry(
    retry=retry_if_exception_type((aiosmtplib.SMTPException, ConnectionError, TimeoutError, OSError)),
    wait=wait_exponential(multiplier=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def send_email(to: str, subject: str, html_body: str, text_body: str) -> None:
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
        timeout=30,
    )
    logger.info("Email envoyé", extra={"extra_fields": {"to": to, "subject": subject}})
