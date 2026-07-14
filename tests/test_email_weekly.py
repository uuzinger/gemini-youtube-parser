from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config.models import Video, WeeklyVideoEntry
from services.email import EmailService


def _config(**overrides):
    values = {
        "dry_run": False,
        "sender_email": "monitor@example.com",
        "smtp_server": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "monitor@example.com",
        "smtp_password": "example-password",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _entry(video_id: str, title: str, published_at: str) -> WeeklyVideoEntry:
    return WeeklyVideoEntry(
        channel_name="Example Channel",
        video=Video(
            id=video_id,
            title=title,
            channel_id="UCxxxx",
            published_at=published_at,
        ),
        duration="10:00",
        exec_summary="Exec summary text",
        detailed_summary="- Detailed bullet one\n- Detailed bullet two",
    )


@pytest.mark.asyncio
async def test_weekly_digest_sent_to_single_recipient(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config())
    entries = [
        _entry("v1", "First Video", "2026-07-06T00:00:00Z"),
        _entry("v2", "Second Video", "2026-07-08T00:00:00Z"),
    ]

    sent = await service.send_weekly_digest(
        ["recipient@example.com"], "Weekly YouTube Digest (2 video(s))", entries
    )

    assert sent is True
    message = send.await_args.args[0]
    assert message["To"] == "recipient@example.com"
    assert message["Subject"] == "Weekly YouTube Digest (2 video(s))"
    body = message.get_payload()[0].get_payload(decode=True).decode("utf-8")
    assert "First Video" in body
    assert "Second Video" in body
    # Videos should appear in the order passed in (chronological).
    assert body.index("First Video") < body.index("Second Video")


@pytest.mark.asyncio
async def test_weekly_digest_not_sent_during_dry_run(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config(dry_run=True))

    sent = await service.send_weekly_digest(
        ["recipient@example.com"], "Subject", [_entry("v1", "Title", "2026-07-06T00:00:00Z")]
    )

    assert sent is False
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_digest_skipped_with_no_recipients(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config())

    sent = await service.send_weekly_digest(
        [], "Subject", [_entry("v1", "Title", "2026-07-06T00:00:00Z")]
    )

    assert sent is False
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_digest_skipped_with_no_entries(monkeypatch) -> None:
    send = AsyncMock()
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config())

    sent = await service.send_weekly_digest(["recipient@example.com"], "Subject", [])

    assert sent is False
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_weekly_digest_send_failure_does_not_raise(monkeypatch) -> None:
    send = AsyncMock(side_effect=RuntimeError("SMTP unavailable"))
    monkeypatch.setattr("services.email.aiosmtplib.send", send)
    service = EmailService(_config())

    sent = await service.send_weekly_digest(
        ["recipient@example.com"], "Subject", [_entry("v1", "Title", "2026-07-06T00:00:00Z")]
    )

    assert sent is False
