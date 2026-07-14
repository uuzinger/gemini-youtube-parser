from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.email import EmailService


def _config(**overrides):
    values = {
        "alerts_enabled": True,
        "alert_subject_prefix": "[YT-Monitor ALERT]",
        "default_recipients": ["admin@example.com"],
        "dry_run": False,
        "sender_email": "monitor@example.com",
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "monitor@example.com",
        "smtp_password": "example-password",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_admin_alert_uses_default_recipients(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config())

    sent = await service.send_admin_alert("quota exceeded", "Details")

    assert sent is True
    message = send.await_args.args[0]
    assert message["To"] == "admin@example.com"
    assert message["Subject"] == "[YT-Monitor ALERT] quota exceeded"


@pytest.mark.asyncio
async def test_admin_alert_is_not_sent_during_dry_run(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config(dry_run=True))

    sent = await service.send_admin_alert("problem", "Details")

    assert sent is False
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_alert_send_failure_does_not_raise(monkeypatch) -> None:
    send = AsyncMock(side_effect=RuntimeError("SMTP unavailable"))
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config())

    sent = await service.send_admin_alert("problem", "Details")

    assert sent is False
