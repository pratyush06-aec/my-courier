"""
WhatsApp notification client — sends messages via Twilio's WhatsApp API.

Design decisions:
- Uses the Twilio Python SDK rather than raw HTTP for cleaner error handling
  and automatic request signing.
- Message templates are concise because WhatsApp messages have a practical
  character limit and are consumed on mobile.
- In production, credentials come from Secrets Manager.  In dev, they come
  from .env / config.
"""

from __future__ import annotations

from typing import Optional

from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from config import config
from models import ProcessedEmail
from utils import get_logger, retry

logger = get_logger(__name__)

# Module-level Twilio client cache.
_twilio_client: Optional[TwilioClient] = None


def _get_client(
    account_sid: str | None = None,
    auth_token: str | None = None,
) -> TwilioClient:
    """Returns a cached Twilio client instance."""
    global _twilio_client
    if _twilio_client is None:
        sid = account_sid or config.TWILIO_ACCOUNT_SID
        token = auth_token or config.TWILIO_AUTH_TOKEN
        if not sid or not token:
            raise ValueError("Twilio credentials are not configured")
        _twilio_client = TwilioClient(sid, token)
    return _twilio_client


def _format_message(email: ProcessedEmail) -> str:
    """
    Formats a concise WhatsApp notification message.
    Kept short and scannable for mobile consumption.
    """
    priority_emoji = {
        "Low": "🟢",
        "Normal": "🔵",
        "High": "🟠",
        "Urgent": "🔴",
    }
    emoji = priority_emoji.get(email.priority, "⚪")

    lines = [
        "📧 *AWS Builder Center Email*",
        "",
        f"*From:* {email.sender}",
        f"*Subject:* {email.subject}",
        f"*Priority:* {emoji} {email.priority}",
    ]

    if email.deadline:
        lines.append(f"*Deadline:* {email.deadline}")

    lines.append(f"\n*Summary:* {email.one_line_summary}")

    if email.action_items:
        lines.append("\n*Action Items:*")
        for item in email.action_items:
            lines.append(f"  • {item}")

    return "\n".join(lines)


@retry(max_attempts=3, base_delay=2.0, exceptions=(TwilioRestException,))
def send_notification(
    email: ProcessedEmail,
    from_number: str | None = None,
    to_number: str | None = None,
    account_sid: str | None = None,
    auth_token: str | None = None,
) -> bool:
    """
    Sends a WhatsApp message via Twilio.

    Args:
        email:       The processed email data.
        from_number: Twilio WhatsApp sender (e.g. "whatsapp:+14155238886").
        to_number:   Recipient WhatsApp number (e.g. "whatsapp:+1234567890").
        account_sid: Twilio Account SID (overrides config).
        auth_token:  Twilio Auth Token (overrides config).

    Returns:
        True if the message was sent successfully.
    """
    sender = from_number or config.TWILIO_FROM_NUMBER
    recipient = to_number or config.TWILIO_TO_NUMBER

    if not sender or not recipient:
        logger.warning("Twilio numbers not configured; skipping WhatsApp notification")
        return False

    client = _get_client(account_sid, auth_token)
    body = _format_message(email)

    message = client.messages.create(
        from_=sender,
        to=recipient,
        body=body,
    )

    logger.info(
        "WhatsApp notification sent: sid=%s, status=%s, message_id=%s",
        message.sid,
        message.status,
        email.message_id,
    )
    return True


def reset_client() -> None:
    """Resets the cached Twilio client. Useful in tests."""
    global _twilio_client
    _twilio_client = None
