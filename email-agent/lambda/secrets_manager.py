"""
AWS Secrets Manager client.

Why Secrets Manager instead of Lambda environment variables?
- Environment variables are visible in the AWS Console to anyone with Lambda
  read permissions.  Secrets Manager provides encryption-at-rest, automatic
  rotation, and fine-grained IAM access control.
- We cache the secret for the lifetime of the Lambda execution context so we
  only pay for one API call per cold start, not per invocation.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

from config import config
from utils import get_logger, retry

logger = get_logger(__name__)

# Module-level cache — survives across warm Lambda invocations.
_secret_cache: Dict[str, Dict[str, Any]] = {}


@retry(max_attempts=3, exceptions=(ClientError,))
def get_secret(secret_name: str) -> Dict[str, Any]:
    """
    Retrieves a secret from AWS Secrets Manager and caches it.

    Args:
        secret_name: The name or ARN of the secret.

    Returns:
        Parsed JSON dict of the secret value.

    Raises:
        ClientError: If the secret cannot be retrieved after retries.
    """
    if secret_name in _secret_cache:
        logger.info("Returning cached secret: %s", secret_name)
        return _secret_cache[secret_name]

    logger.info("Fetching secret from Secrets Manager: %s", secret_name)
    client = boto3.client("secretsmanager", region_name=config.AWS_REGION)

    response = client.get_secret_value(SecretId=secret_name)
    secret_value: Dict[str, Any] = json.loads(response["SecretString"])

    _secret_cache[secret_name] = secret_value
    return secret_value


def get_gmail_credentials() -> Dict[str, Any]:
    """
    Retrieves Gmail OAuth credentials from Secrets Manager.
    Expected secret JSON structure:
    {
        "client_id": "...",
        "client_secret": "...",
        "refresh_token": "...",
        "token_uri": "https://oauth2.googleapis.com/token"
    }
    """
    return get_secret("email-agent/gmail-credentials")


def get_slack_webhook_url() -> str:
    """Retrieves the Slack webhook URL from Secrets Manager."""
    secret = get_secret("email-agent/slack")
    return secret["webhook_url"]


def get_twilio_credentials() -> Dict[str, str]:
    """
    Retrieves Twilio credentials from Secrets Manager.
    Expected secret JSON structure:
    {
        "account_sid": "...",
        "auth_token": "...",
        "from_number": "whatsapp:+14155238886",
        "to_number": "whatsapp:+1234567890"
    }
    """
    return get_secret("email-agent/twilio")


def clear_cache() -> None:
    """Clears the secret cache. Useful in tests."""
    _secret_cache.clear()
