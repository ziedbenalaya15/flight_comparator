from types import SimpleNamespace

import pytest

from app.services import mailer


def _settings(**overrides):
    values = {
        "resend_api_key": "",
        "resend_from_email": "Flight Alerts <onboarding@resend.dev>",
        "gmail_smtp_user": "",
        "gmail_app_password": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_resend_is_preferred_over_smtp(monkeypatch):
    monkeypatch.setattr(
        mailer,
        "get_settings",
        lambda: _settings(
            resend_api_key="re_test",
            gmail_smtp_user="sender@example.com",
            gmail_app_password="secret",
        ),
    )

    assert mailer.email_provider() == "resend"
    assert mailer.email_configured() is True
    assert mailer.smtp_configured() is True


def test_no_email_provider(monkeypatch):
    monkeypatch.setattr(mailer, "get_settings", lambda: _settings())

    assert mailer.email_provider() is None
    assert mailer.email_configured() is False


def test_resend_idempotency_key_is_stable_and_content_sensitive():
    first = mailer._resend_idempotency_key("to@example.com", "Sujet", "Corps")
    same = mailer._resend_idempotency_key("to@example.com", "Sujet", "Corps")
    changed = mailer._resend_idempotency_key("to@example.com", "Sujet", "Autre")

    assert first == same
    assert first != changed
    assert first.startswith("flight-alert-")


@pytest.mark.asyncio
async def test_send_email_uses_resend(monkeypatch):
    monkeypatch.setattr(mailer, "get_settings", lambda: _settings(resend_api_key="re_test"))
    sent = {}

    async def fake_resend(to, subject, html_body, text_body):
        sent.update(to=to, subject=subject, html=html_body, text=text_body)

    monkeypatch.setattr(mailer, "_send_resend", fake_resend)

    provider = await mailer.send_email("to@example.com", "Sujet", "<p>HTML</p>", "Texte")

    assert provider == "resend"
    assert sent["to"] == "to@example.com"
