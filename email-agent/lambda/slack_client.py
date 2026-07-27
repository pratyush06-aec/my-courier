"""
Slack notification client — sends rich, formatted messages via Incoming Webhooks.

Design decisions:
- Uses Slack *Block Kit* for visually appealing messages with sections,
  dividers, and context blocks.  Plain-text ``text`` is set as a fallback
  for notification previews and accessibility.
- The webhook URL is retrieved from Secrets Manager in production or from
  the config (env var) in local development.
"""

from __future__ import annotations

from typing import Any, Dict, List

import requests

from config import config
from models import ProcessedEmail
from utils import get_logger, retry

logger = get_logger(__name__)


def _build_slack_blocks(email: ProcessedEmail) -> List[Dict[str, Any]]:
    """
    Constructs Slack Block Kit blocks for a professional notification layout.
    """
    priority_emoji = {
        "Low": "🟢",
        "Normal": "🔵",
        "High": "🟠",
        "Urgent": "🔴",
    }
    emoji = priority_emoji.get(email.priority, "⚪")

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📧 New Email from AWS Builder Center",
                "emoji": True,
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*From:*\n{email.sender}"},
                {"type": "mrkdwn", "text": f"*Subject:*\n{email.subject}"},
            ],
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Priority:*\n{emoji} {email.priority}"},
                {
                    "type": "mrkdwn",
                    "text": f"*Deadline:*\n{email.deadline or 'None identified'}",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📝 Summary:*\n{email.one_line_summary}",
            },
        },
    ]

    # Action items (if any)
    if email.action_items:
        action_text = "\n".join(f"• {item}" for item in email.action_items)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*✅ Action Items:*\n{action_text}",
                },
            }
        )

    # Footer context
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Processed at {email.processed_at} | Message ID: {email.message_id}",
                }
            ],
        }
    )

    return blocks


@retry(max_attempts=3, base_delay=1.0, exceptions=(requests.RequestException,))
def send_notification(email: ProcessedEmail, webhook_url: str | None = None) -> bool:
    """
    Sends a formatted notification to Slack via Incoming Webhook.

    Args:
        email:       The processed email data.
        webhook_url: Slack webhook URL. If None, reads from config.

    Returns:
        True if the message was posted successfully.
    """
    url = webhook_url or config.SLACK_WEBHOOK_URL
    if not url:
        logger.warning("Slack webhook URL not configured; skipping notification")
        return False

    payload: Dict[str, Any] = {
        "text": f"📧 New email from {email.sender}: {email.subject}",
        "blocks": _build_slack_blocks(email),
    }

    response = requests.post(url, json=payload, timeout=10)

    if response.status_code == 200 and response.text == "ok":
        logger.info("Slack notification sent for message_id=%s", email.message_id)
        return True

    logger.error(
        "Slack webhook returned %d: %s", response.status_code, response.text
    )
    response.raise_for_status()
    return False
