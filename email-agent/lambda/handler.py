"""
AWS Lambda handler — the main orchestration entry point.

This module is the "glue" that wires together all the individual clients.
It follows the *Controller* pattern: receive event, validate, delegate to
specialised modules, and return a response.

Flow:
1. Receive Google Pub/Sub push notification via API Gateway.
2. Decode the Pub/Sub message to extract historyId.
3. Fetch new messages from Gmail using the History API.
4. For each new message:
   a. Check for duplicate (DynamoDB).
   b. Validate sender against allow-list.
   c. Summarise with Amazon Bedrock.
   d. Store in DynamoDB.
   e. Notify Slack.
   f. Notify WhatsApp.
5. Return HTTP 200 to acknowledge the Pub/Sub message.
   (Returning non-200 would cause Pub/Sub to retry the push.)
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict

from config import config
from models import EmailMetadata, ProcessedEmail, PubSubNotification
from utils import extract_email_address, get_logger

import gmail_client
import bedrock_client
import dynamodb_client
import slack_client
import whatsapp_client
import secrets_manager

logger = get_logger(__name__)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda entry point invoked by API Gateway.

    The event body is a Google Pub/Sub push message:
    {
        "message": {
            "data": "<base64-encoded JSON>",
            "messageId": "...",
            "publishTime": "..."
        },
        "subscription": "..."
    }

    The base64-decoded data contains:
    {
        "emailAddress": "user@gmail.com",
        "historyId": 123456
    }
    """
    logger.info("Lambda invoked", extra={"extra_data": {"event_keys": list(event.keys())}})

    try:
        # ── Step 1: Parse the incoming event ─────────────────────────────
        pubsub_notification = _parse_pubsub_event(event)
        if pubsub_notification is None:
            return _response(200, "No valid Pub/Sub message found; acknowledged.")

        logger.info(
            "Pub/Sub notification received",
            extra={"extra_data": {
                "email_address": pubsub_notification.email_address,
                "history_id": pubsub_notification.history_id,
            }},
        )

        # ── Step 2: Authenticate with Gmail ──────────────────────────────
        try:
            gmail_secret = secrets_manager.get_gmail_credentials()
            service = gmail_client.get_service(secret=gmail_secret)
        except Exception:
            # Fallback for local development
            logger.warning("Secrets Manager unavailable; using local credentials")
            service = gmail_client.get_service()

        # ── Step 3: Fetch new messages since historyId ───────────────────
        message_ids = gmail_client.fetch_new_messages(
            service, pubsub_notification.history_id
        )

        if not message_ids:
            logger.info("No new messages found")
            return _response(200, "No new messages.")

        processed_count = 0

        for msg_id in message_ids:
            try:
                processed = _process_single_message(service, msg_id)
                if processed:
                    processed_count += 1
            except Exception as exc:
                # Log and continue — don't let one bad message block others.
                logger.error(
                    "Failed to process message %s: %s", msg_id, exc, exc_info=True
                )

        return _response(200, f"Processed {processed_count}/{len(message_ids)} messages.")

    except Exception as exc:
        logger.error("Unhandled error in handler: %s", exc, exc_info=True)
        # Return 200 anyway to prevent Pub/Sub from retrying indefinitely.
        # The error is logged to CloudWatch for investigation.
        return _response(200, "Error occurred but acknowledged to prevent retry loop.")


def _process_single_message(service: Any, message_id: str) -> bool:
    """
    Processes a single Gmail message through the full pipeline.

    Returns:
        True if the message was successfully processed and notifications sent.
        False if skipped (duplicate or filtered sender).
    """
    # ── Deduplication check ───────────────────────────────────────────
    if dynamodb_client.is_duplicate(message_id):
        logger.info("Skipping duplicate message: %s", message_id)
        return False

    # ── Fetch full email metadata ─────────────────────────────────────
    email: EmailMetadata = gmail_client.get_email_metadata(service, message_id)
    logger.info(
        "Email fetched",
        extra={"extra_data": {
            "message_id": email.message_id,
            "sender": email.sender_email,
            "subject": email.subject,
        }},
    )

    # ── Sender validation ─────────────────────────────────────────────
    allowed = [s.strip().lower() for s in config.ALLOWED_SENDERS]
    if email.sender_email not in allowed:
        logger.info(
            "Sender %s not in allow-list; skipping", email.sender_email
        )
        return False

    # ── AI summarisation ──────────────────────────────────────────────
    summary = bedrock_client.summarise_email(email)

    # ── Build the processed record ────────────────────────────────────
    processed = ProcessedEmail(
        message_id=email.message_id,
        thread_id=email.thread_id,
        sender=email.sender,
        sender_email=email.sender_email,
        subject=email.subject,
        date=email.date,
        snippet=email.snippet,
        priority=summary.priority,
        deadline=summary.deadline,
        action_items=summary.action_items,
        one_line_summary=summary.one_line_summary,
    )

    # ── Persist to DynamoDB ───────────────────────────────────────────
    stored = dynamodb_client.store_email(processed)
    if not stored:
        # Another invocation already stored it (race condition handled).
        logger.info("Message %s already stored by another invocation", message_id)
        return False

    # ── Notifications ─────────────────────────────────────────────────
    # Slack
    try:
        slack_url = _get_slack_url()
        slack_ok = slack_client.send_notification(processed, webhook_url=slack_url)
        processed.notified_slack = slack_ok
    except Exception as exc:
        logger.error("Slack notification failed: %s", exc)

    # WhatsApp
    try:
        twilio_creds = _get_twilio_creds()
        whatsapp_ok = whatsapp_client.send_notification(
            processed,
            from_number=twilio_creds.get("from_number"),
            to_number=twilio_creds.get("to_number"),
            account_sid=twilio_creds.get("account_sid"),
            auth_token=twilio_creds.get("auth_token"),
        )
        processed.notified_whatsapp = whatsapp_ok
    except Exception as exc:
        logger.error("WhatsApp notification failed: %s", exc)

    logger.info(
        "Message fully processed",
        extra={"extra_data": {
            "message_id": processed.message_id,
            "priority": processed.priority,
            "slack": processed.notified_slack,
            "whatsapp": processed.notified_whatsapp,
        }},
    )
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_pubsub_event(event: Dict[str, Any]) -> PubSubNotification | None:
    """
    Extracts and decodes the Pub/Sub notification from the API Gateway event.

    API Gateway sends the Pub/Sub push body as a JSON string in
    ``event["body"]``.
    """
    body_str = event.get("body", "")
    if not body_str:
        logger.warning("Event body is empty")
        return None

    # API Gateway may deliver the body as a string that needs parsing.
    if isinstance(body_str, str):
        body = json.loads(body_str)
    else:
        body = body_str

    message = body.get("message", {})
    data_b64 = message.get("data", "")
    if not data_b64:
        logger.warning("No 'data' field in Pub/Sub message")
        return None

    decoded = json.loads(base64.b64decode(data_b64).decode("utf-8"))

    return PubSubNotification(
        email_address=decoded.get("emailAddress", ""),
        history_id=int(decoded.get("historyId", 0)),
    )


def _get_slack_url() -> str:
    """Gets the Slack webhook URL, preferring Secrets Manager."""
    try:
        return secrets_manager.get_slack_webhook_url()
    except Exception:
        return config.SLACK_WEBHOOK_URL


def _get_twilio_creds() -> Dict[str, str]:
    """Gets Twilio credentials, preferring Secrets Manager."""
    try:
        return secrets_manager.get_twilio_credentials()
    except Exception:
        return {
            "account_sid": config.TWILIO_ACCOUNT_SID,
            "auth_token": config.TWILIO_AUTH_TOKEN,
            "from_number": config.TWILIO_FROM_NUMBER,
            "to_number": config.TWILIO_TO_NUMBER,
        }


def _response(status_code: int, message: str) -> Dict[str, Any]:
    """Builds a standard API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": message}),
    }


# ---------------------------------------------------------------------------
# Watch Renewal Handler (invoked by EventBridge every 6 days)
# ---------------------------------------------------------------------------

def renew_watch_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Separate Lambda entry point that renews the Gmail push notification watch.

    Gmail watches expire after 7 days.  An EventBridge rule triggers this
    handler every 6 days to ensure continuous monitoring without gaps.
    """
    import os

    topic_name = os.getenv("PUBSUB_TOPIC_NAME", "")
    if not topic_name:
        logger.error("PUBSUB_TOPIC_NAME not set; cannot renew watch")
        return {"statusCode": 500, "body": "PUBSUB_TOPIC_NAME not configured"}

    try:
        gmail_secret = secrets_manager.get_gmail_credentials()
        service = gmail_client.get_service(secret=gmail_secret)
    except Exception:
        logger.warning("Secrets Manager unavailable; using local credentials")
        service = gmail_client.get_service()

    result = gmail_client.setup_watch(service, topic_name)
    logger.info("Watch renewed successfully: %s", result)
    return {"statusCode": 200, "body": json.dumps(result, default=str)}
