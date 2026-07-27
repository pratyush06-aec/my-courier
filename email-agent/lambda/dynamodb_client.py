"""
DynamoDB client — handles duplicate detection and metadata persistence.

Design decisions:
- Uses a *conditional put* with ``attribute_not_exists(message_id)`` to
  atomically prevent duplicate processing.  This is cheaper and simpler
  than a read-then-write pattern and is safe under concurrent invocations.
- TTL is set to 30 days so old records are automatically cleaned up by
  DynamoDB at no cost.
"""

from __future__ import annotations

import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from config import config
from models import ProcessedEmail
from utils import get_logger, retry

logger = get_logger(__name__)

# Module-level DynamoDB resource (reused across warm invocations).
_table = None

# TTL: 30 days in seconds
TTL_DAYS = 30


def _get_table():
    """Returns a cached DynamoDB Table resource."""
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb", region_name=config.AWS_REGION)
        _table = dynamodb.Table(config.DYNAMODB_TABLE_NAME)
    return _table


def is_duplicate(message_id: str) -> bool:
    """
    Checks whether a message has already been processed.

    Returns:
        True if the message_id already exists in the table.
    """
    try:
        response = _get_table().get_item(Key={"message_id": message_id})
        exists = "Item" in response
        if exists:
            logger.info("Duplicate detected: message_id=%s", message_id)
        return exists
    except ClientError as exc:
        logger.error("DynamoDB get_item failed: %s", exc)
        raise


@retry(max_attempts=3, exceptions=(ClientError,))
def store_email(email: ProcessedEmail) -> bool:
    """
    Stores processed email metadata in DynamoDB with a conditional write
    to prevent duplicates.

    Uses ``attribute_not_exists`` as an atomic guard — if two Lambda
    invocations race for the same message_id, only one succeeds.

    Args:
        email: The fully populated ProcessedEmail model.

    Returns:
        True if the item was stored; False if it already existed.
    """
    ttl_epoch = int(
        (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=TTL_DAYS)
        ).timestamp()
    )

    item = email.model_dump()
    item["ttl"] = ttl_epoch  # DynamoDB TTL attribute

    try:
        _get_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(message_id)",
        )
        logger.info("Stored email: message_id=%s, subject=%s", email.message_id, email.subject)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("Duplicate write prevented for message_id=%s", email.message_id)
            return False
        raise


def reset_table_cache() -> None:
    """Resets the cached table reference. Useful in tests."""
    global _table
    _table = None
