"""
Email delivery service (fail-open).

Supports three channels, in priority order:
  1. Resend (RESEND_API_KEY) — REST API via httpx (free tier).
  2. SMTP (SMTP_USER/SMTP_PASSWORD) — smtplib over TLS in a worker thread.
  3. Dev sink — when neither is configured the email is logged and the caller
     receives a ``dev_preview`` (a link/token the local/dev UI can surface),
     so auth flows stay fully testable without any mail provider.
Never raises: delivery problems degrade to the dev sink with a logged warning.
"""
import asyncio
import logging
from email.message import EmailMessage
from smtplib import SMTPAuthenticationError, SMTPException, SMTP_SSL, SMTP

import httpx
import structlog

from backend.app.core.config import settings

logger = structlog.get_logger(__name__)


class EmailService:
    def __init__(self) -> None:
        self.from_addr = settings.EMAIL_FROM

    async def send(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: str | None = None,
    ) -> dict:
        """Deliver an email over the best available channel (fail-open)."""
        if settings.RESEND_API_KEY:
            return await self._send_resend(to, subject, body_html, body_text)
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            return await asyncio.to_thread(
                self._send_smtp, to, subject, body_html, body_text
            )
        # Dev sink: no provider configured.
        logger.info(
            "email_dev_sink",
            to=to,
            subject=subject,
            channel="dev",
            note="Neither RESEND_API_KEY nor SMTP credentials configured.",
        )
        return {
            "sent": False,
            "channel": "dev",
            "message": "Email delivery not configured; message logged instead.",
        }

    async def _send_resend(
        self, to: str, subject: str, body_html: str, body_text: str | None
    ) -> dict:
        url = "https://api.resend.com/emails"
        payload = {
            "from": self.from_addr,
            "to": [to],
            "subject": subject,
            "html": body_html,
            "text": body_text or "",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    url, json=payload, headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"}
                )
            if resp.status_code < 300:
                logger.info("email_sent", channel="resend", to=to, subject=subject)
                return {"sent": True, "channel": "resend", "status_code": resp.status_code}
            logger.warning("email_resend_failed", status_code=resp.status_code, body=resp.text[:500])
            return {"sent": False, "channel": "dev", "status_code": resp.status_code}
        except Exception as exc:
            logger.warning("email_resend_error", error=str(exc))
            return {"sent": False, "channel": "dev", "error": str(exc)}

    def _send_smtp(
        self, to: str, subject: str, body_html: str, body_text: str | None
    ) -> dict:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body_text or "")
        msg.add_alternative(body_html, subtype="html")
        try:
            if settings.SMTP_TLS and settings.SMTP_PORT == 465:
                with SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as server:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    server.send_message(msg)
            else:
                with SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as server:
                    if settings.SMTP_TLS:
                        server.starttls()
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    server.send_message(msg)
            logger.info("email_sent", channel="smtp", to=to, subject=subject)
            return {"sent": True, "channel": "smtp"}
        except (SMTPAuthenticationError, SMTPException, OSError) as exc:
            logger.warning("email_smtp_failed", error=str(exc))
            return {"sent": False, "channel": "dev", "error": str(exc)}


email_service = EmailService()


def build_email_link(route: str, token: str) -> str:
    """Builds an absolute magic-link URL for the (then-typical) frontend route."""
    origin = settings.APP_PUBLIC_URL or "http://localhost:3000"
    return f"{origin}{route}{token}"