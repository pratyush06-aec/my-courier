"""
Unit tests for the handler module.

Uses unittest.mock to isolate the handler from all external dependencies
(Gmail, DynamoDB, Bedrock, Slack, Twilio, Secrets Manager).
"""

import base64
import json
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda"))

from models import BedrockSummary, EmailMetadata


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_pubsub_event(email_address: str = "test@gmail.com", history_id: int = 12345):
    """Creates a mock API Gateway event containing a Pub/Sub push message."""
    data = json.dumps({
        "emailAddress": email_address,
        "historyId": history_id,
    })
    encoded = base64.b64encode(data.encode()).decode()
    body = json.dumps({
        "message": {
            "data": encoded,
            "messageId": "pubsub-msg-1",
            "publishTime": "2026-07-25T12:00:00Z",
        },
        "subscription": "projects/test/subscriptions/gmail-push",
    })
    return {"body": body}


def _make_email_metadata(
    message_id: str = "msg-001",
    sender_email: str = "studentbuilders@amazon.com",
) -> EmailMetadata:
    return EmailMetadata(
        message_id=message_id,
        thread_id="thread-001",
        sender=f"AWS Builder <{sender_email}>",
        sender_email=sender_email,
        to="me@example.com",
        subject="Weekend Challenge Update",
        date="2026-07-25",
        snippet="Important update about the challenge...",
        body_text="Full email body here.",
    )


def _make_bedrock_summary() -> BedrockSummary:
    return BedrockSummary(
        subject="Weekend Challenge Update",
        sender="AWS Builder",
        priority="High",
        deadline="2026-07-27",
        action_items=["Submit project by Sunday"],
        one_line_summary="AWS Builder Center announces the weekend challenge deadline.",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    """Tests for the main lambda_handler function."""

    @patch("handler.secrets_manager")
    @patch("handler.whatsapp_client")
    @patch("handler.slack_client")
    @patch("handler.dynamodb_client")
    @patch("handler.bedrock_client")
    @patch("handler.gmail_client")
    def test_full_pipeline_success(
        self,
        mock_gmail,
        mock_bedrock,
        mock_dynamo,
        mock_slack,
        mock_whatsapp,
        mock_secrets,
    ):
        """A valid email from an allowed sender should flow through the entire pipeline."""
        # Arrange
        mock_secrets.get_gmail_credentials.side_effect = Exception("No secrets in test")
        mock_gmail.get_service.return_value = MagicMock()
        mock_gmail.fetch_new_messages.return_value = ["msg-001"]
        mock_gmail.get_email_metadata.return_value = _make_email_metadata()
        mock_dynamo.is_duplicate.return_value = False
        mock_dynamo.store_email.return_value = True
        mock_bedrock.summarise_email.return_value = _make_bedrock_summary()
        mock_slack.send_notification.return_value = True
        mock_secrets.get_slack_webhook_url.side_effect = Exception("No secrets")
        mock_secrets.get_twilio_credentials.side_effect = Exception("No secrets")
        mock_whatsapp.send_notification.return_value = True

        from handler import lambda_handler

        # Act
        result = lambda_handler(_make_pubsub_event(), None)

        # Assert
        assert result["statusCode"] == 200
        mock_gmail.fetch_new_messages.assert_called_once()
        mock_bedrock.summarise_email.assert_called_once()
        mock_dynamo.store_email.assert_called_once()

    @patch("handler.secrets_manager")
    @patch("handler.whatsapp_client")
    @patch("handler.slack_client")
    @patch("handler.dynamodb_client")
    @patch("handler.bedrock_client")
    @patch("handler.gmail_client")
    def test_duplicate_message_skipped(
        self,
        mock_gmail,
        mock_bedrock,
        mock_dynamo,
        mock_slack,
        mock_whatsapp,
        mock_secrets,
    ):
        """A duplicate message should be skipped without processing."""
        mock_secrets.get_gmail_credentials.side_effect = Exception("No secrets")
        mock_gmail.get_service.return_value = MagicMock()
        mock_gmail.fetch_new_messages.return_value = ["msg-dup"]
        mock_dynamo.is_duplicate.return_value = True

        from handler import lambda_handler

        result = lambda_handler(_make_pubsub_event(), None)

        assert result["statusCode"] == 200
        mock_bedrock.summarise_email.assert_not_called()
        mock_slack.send_notification.assert_not_called()

    @patch("handler.secrets_manager")
    @patch("handler.whatsapp_client")
    @patch("handler.slack_client")
    @patch("handler.dynamodb_client")
    @patch("handler.bedrock_client")
    @patch("handler.gmail_client")
    def test_unallowed_sender_filtered(
        self,
        mock_gmail,
        mock_bedrock,
        mock_dynamo,
        mock_slack,
        mock_whatsapp,
        mock_secrets,
    ):
        """Emails from senders not on the allow-list should be filtered out."""
        mock_secrets.get_gmail_credentials.side_effect = Exception("No secrets")
        mock_gmail.get_service.return_value = MagicMock()
        mock_gmail.fetch_new_messages.return_value = ["msg-spam"]
        mock_gmail.get_email_metadata.return_value = _make_email_metadata(
            sender_email="random@outsider.com"
        )
        mock_dynamo.is_duplicate.return_value = False

        from handler import lambda_handler

        result = lambda_handler(_make_pubsub_event(), None)

        assert result["statusCode"] == 200
        mock_bedrock.summarise_email.assert_not_called()

    def test_empty_body_acknowledged(self):
        """An event with no body should return 200 (acknowledge to prevent Pub/Sub retry)."""
        from handler import lambda_handler

        result = lambda_handler({"body": ""}, None)
        assert result["statusCode"] == 200


class TestParsePubSubEvent:
    """Tests for the _parse_pubsub_event helper."""

    def test_valid_event(self):
        from handler import _parse_pubsub_event

        event = _make_pubsub_event("user@gmail.com", 99999)
        result = _parse_pubsub_event(event)

        assert result is not None
        assert result.email_address == "user@gmail.com"
        assert result.history_id == 99999

    def test_missing_data_returns_none(self):
        from handler import _parse_pubsub_event

        event = {"body": json.dumps({"message": {}})}
        result = _parse_pubsub_event(event)
        assert result is None
