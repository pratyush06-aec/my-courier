"""
Gmail API client — handles OAuth authentication and email retrieval.

Design decisions:
- Uses *refresh tokens* exclusively in production.  The initial OAuth consent
  flow is performed once locally; the resulting refresh token is stored in
  AWS Secrets Manager.
- ``get_service()`` builds a Gmail API service object using cached credentials
  so it can be reused within the same Lambda invocation.
- ``fetch_new_messages()`` uses the Gmail *history* API to efficiently
  retrieve only messages that arrived after a given historyId (supplied by
  the Pub/Sub push notification).  This avoids re-scanning the entire inbox.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build, Resource

from config import config
from models import EmailMetadata
from utils import (
    decode_base64url,
    extract_email_address,
    get_logger,
    retry,
    safe_get_header,
)

logger = get_logger(__name__)

# Module-level cache for the Gmail service object.
_gmail_service: Optional[Resource] = None


def _build_credentials_from_secret(secret: Dict[str, Any]) -> Credentials:
    """
    Constructs a ``google.oauth2.credentials.Credentials`` object from the
    secret dict stored in AWS Secrets Manager.
    """
    creds = Credentials(
        token=None,
        refresh_token=secret["refresh_token"],
        token_uri=secret.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
    )
    # Force an immediate refresh so we have a valid access token.
    creds.refresh(Request())
    return creds


def _build_credentials_local() -> Credentials:
    """
    Loads credentials from local token.json (for development only).
    Falls back to running the OAuth consent flow if no token exists.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    token_path = config.GMAIL_TOKEN_JSON_PATH
    creds_path = config.GMAIL_CREDENTIALS_JSON_PATH

    creds: Optional[Credentials] = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        # Persist for next run
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def get_service(secret: Optional[Dict[str, Any]] = None) -> Resource:
    """
    Returns a cached Gmail API service object.

    Args:
        secret: Gmail OAuth secret dict (from Secrets Manager).
                If None, falls back to local file-based credentials.
    """
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    if secret:
        creds = _build_credentials_from_secret(secret)
    else:
        creds = _build_credentials_local()

    _gmail_service = build("gmail", "v1", credentials=creds)
    logger.info("Gmail service built successfully")
    return _gmail_service


# ---------------------------------------------------------------------------
# Email retrieval
# ---------------------------------------------------------------------------

@retry(max_attempts=3)
def fetch_new_messages(
    service: Resource,
    history_id: int,
    user_id: str = "me",
) -> List[str]:
    """
    Uses the Gmail History API to find message IDs added since *history_id*.

    Falls back to ``messages.list`` with a recent-messages query if the
    History API returns no results.  This handles a well-known edge case
    where the Pub/Sub historyId is already at or past the new message,
    causing ``history.list`` to return an empty delta.

    Returns:
        A list of Gmail message IDs.
    """
    message_ids: List[str] = []

    # --- Primary: History API ---
    try:
        response = (
            service.users()
            .history()
            .list(userId=user_id, startHistoryId=history_id, historyTypes=["messageAdded"])
            .execute()
        )

        history_records = response.get("history", [])
        for record in history_records:
            for msg in record.get("messagesAdded", []):
                msg_id = msg["message"]["id"]
                if msg_id not in message_ids:
                    message_ids.append(msg_id)

        logger.info(
            "History API found %d message(s) since historyId %d",
            len(message_ids),
            history_id,
        )
    except Exception as exc:
        logger.warning("History API failed (%s); falling back to messages.list", exc)

    # --- Fallback: messages.list (recent unread in inbox) ---
    if not message_ids:
        try:
            logger.info("Falling back to messages.list for recent messages")
            response = (
                service.users()
                .messages()
                .list(userId=user_id, q="is:unread in:inbox", maxResults=5)
                .execute()
            )
            for msg in response.get("messages", []):
                if msg["id"] not in message_ids:
                    message_ids.append(msg["id"])
            logger.info(
                "messages.list fallback found %d message(s)",
                len(message_ids),
            )
        except Exception as exc:
            logger.error("messages.list fallback also failed: %s", exc)
            raise

    return message_ids


@retry(max_attempts=3)
def get_email_metadata(
    service: Resource,
    message_id: str,
    user_id: str = "me",
) -> EmailMetadata:
    """
    Fetches the full message and parses it into an ``EmailMetadata`` model.
    """
    msg: Dict[str, Any] = (
        service.users()
        .messages()
        .get(userId=user_id, id=message_id, format="full")
        .execute()
    )

    headers = msg.get("payload", {}).get("headers", [])
    from_header = safe_get_header(headers, "From")

    # Extract the plain-text body
    body_text = _extract_body(msg.get("payload", {}), mime_type="text/plain")
    body_html = _extract_body(msg.get("payload", {}), mime_type="text/html") if not body_text else ""

    return EmailMetadata(
        message_id=msg["id"],
        thread_id=msg.get("threadId", ""),
        sender=from_header,
        sender_email=extract_email_address(from_header),
        to=safe_get_header(headers, "To"),
        subject=safe_get_header(headers, "Subject"),
        date=safe_get_header(headers, "Date"),
        snippet=msg.get("snippet", ""),
        body_text=body_text,
        body_html=body_html,
        labels=msg.get("labelIds", []),
    )


def _extract_body(payload: Dict[str, Any], mime_type: str = "text/plain") -> str:
    """
    Recursively walks the MIME tree to find and decode the body part matching
    the requested mime_type.

    Gmail messages can be:
    - Simple (body directly on payload)
    - Multipart (body in nested parts)
    """
    # Direct body
    if payload.get("mimeType") == mime_type:
        data = payload.get("body", {}).get("data", "")
        if data:
            return decode_base64url(data)

    # Recurse into multipart parts
    for part in payload.get("parts", []):
        result = _extract_body(part, mime_type)
        if result:
            return result

    return ""


# ---------------------------------------------------------------------------
# Gmail Push Notification Setup
# ---------------------------------------------------------------------------

@retry(max_attempts=2)
def setup_watch(
    service: Resource,
    topic_name: str,
    user_id: str = "me",
) -> Dict[str, Any]:
    """
    Calls ``users.watch()`` to register Gmail push notifications.

    This must be called at least once every 7 days to keep the watch active.
    In production, an EventBridge scheduled rule triggers a separate Lambda
    to renew the watch.

    Args:
        service:    Authenticated Gmail API service.
        topic_name: Google Cloud Pub/Sub topic (e.g. "projects/my-proj/topics/gmail-push").
        user_id:    Gmail user (default "me").

    Returns:
        API response containing historyId and expiration.
    """
    request_body = {
        "topicName": topic_name,
        "labelIds": ["INBOX"],
    }
    response = service.users().watch(userId=user_id, body=request_body).execute()
    logger.info(
        "Gmail watch registered. historyId=%s, expiration=%s",
        response.get("historyId"),
        response.get("expiration"),
    )
    return response
