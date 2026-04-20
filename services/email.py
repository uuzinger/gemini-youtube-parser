from __future__ import annotations

import html
import logging

import markdown
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config.models import Config, Video
from .exceptions import EmailError

logger = logging.getLogger(__name__)


class EmailService:
    """Async email notification service."""

    def __init__(self, config: Config):
        self.config = config

    async def send_notification(
        self,
        channel_name: str,
        video: Video,
        duration: str,
        exec_summary: str,
        detailed_summary: str,
        key_quotes: str,
    ) -> None:
        """Send email notification with video summary."""
        if not self.config.default_recipients:
            logger.warning(
                "No 'default_recipients' configured. Skipping email."
            )
            return

        bcc_recipients = self.config.channel_recipients.get(
            video.channel_id, []
        )
        all_emails = list(
            set(self.config.default_recipients + bcc_recipients)
        )
        if not all_emails:
            logger.warning(
                "No recipients for channel %s and no defaults. Skipping email.",
                video.channel_id,
            )
            return

        subject = (
            f"New YouTube Summary: [{channel_name}] {video.title}"
        )

        exec_html = markdown.markdown(exec_summary or "")
        detailed_html = markdown.markdown(detailed_summary or "")
        quotes_html = markdown.markdown(key_quotes or "")

        body_html = f"""
        <html><body>
            <p>A new video has been posted on the '{html.escape(channel_name)}' channel:</p>
            <p>
                <b>Title:</b> {html.escape(video.title)}<br>
                <b>Duration:</b> {html.escape(duration)}<br>
                <b>Link:</b> <a href="https://www.youtube.com/watch?v={video.id}">https://www.youtube.com/watch?v={video.id}</a>
            </p><hr>
            <h2>Executive Summary</h2><div>{exec_html}</div><hr>
            <h2>Detailed Summary</h2><div>{detailed_html}</div><hr>
            <h2>Key Quotes</h2><div>{quotes_html}</div>
        </body></html>
        """

        msg = MIMEMultipart("alternative")
        msg["From"] = self.config.sender_email
        msg["To"] = ", ".join(self.config.default_recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=self.config.smtp_server,
                port=self.config.smtp_port,
                username=self.config.smtp_user,
                password=self.config.smtp_password,
                start_tls=True,
            )
            log_msg = (
                f"Email sent for {video.id} to "
                f"{self.config.default_recipients}"
            )
            if bcc_recipients:
                log_msg += f" (BCC: {bcc_recipients})"
            logger.info(log_msg)
        except Exception as e:
            logger.error("Failed to send email for %s: %s", video.id, e)
            raise EmailError(f"Email send failed: {e}") from e
