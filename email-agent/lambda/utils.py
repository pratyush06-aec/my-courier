"""
Utility helpers: structured logging, retry decorator, and email parsing.

Design decisions:
- Structured JSON logging lets CloudWatch Insights query logs with SQL-like syntax.
- A generic retry decorator centralises backoff logic so every client module
  does not re-implement its own retry loop.
- Email parsing helpers keep gmail_client.py focused on API interaction.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Structured JSON Logging
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects.
    CloudWatch natively parses JSON log lines, enabling powerful queries
    via CloudWatch Logs Insights (e.g. filter by level, message_id, etc.).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra fields passed via `logger.info("msg", extra={...})`
        if hasattr(record, "extra_data"):
            log_entry.update(record.extra_data)
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


def get_logger(name: str) -> logging.Logger:
    """
    Returns a logger pre-configured with JSON formatting.
    Call once per module:  ``logger = get_logger(__name__)``
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ---------------------------------------------------------------------------
# Retry Decorator with Exponential Backoff
# ---------------------------------------------------------------------------

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator that retries a function on failure with exponential backoff.

    Args:
        max_attempts: Total number of attempts (including the first call).
        base_delay:   Initial delay in seconds.
        max_delay:    Cap on the delay between retries.
        exceptions:   Tuple of exception classes that should trigger a retry.

    Why exponential backoff?
    External APIs (Gmail, Slack, Twilio, Bedrock) may transiently fail.
    Retrying immediately can exacerbate rate-limit issues; exponential
    backoff gives the remote service time to recover.
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = get_logger(func.__module__)
            last_exception: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt == max_attempts:
                        logger.error(
                            "All %d attempts failed for %s: %s",
                            max_attempts, func.__name__, exc,
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "Attempt %d/%d for %s failed (%s). Retrying in %.1fs…",
                        attempt, max_attempts, func.__name__, exc, delay,
                    )
                    time.sleep(delay)
            # Unreachable, but keeps mypy happy
            raise last_exception  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


# ---------------------------------------------------------------------------
# Email Parsing Helpers
# ---------------------------------------------------------------------------

def decode_base64url(data: str) -> str:
    """
    Decodes a base64url-encoded string (used by the Gmail API for message bodies).
    Gmail uses URL-safe base64 *without* padding; we must restore padding before
    decoding.
    """
    padded = data + "=" * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def extract_email_address(from_header: str) -> str:
    """
    Extracts a bare email address from a From header value.

    Examples:
        "John Doe <john@example.com>"  ->  "john@example.com"
        "john@example.com"             ->  "john@example.com"
    """
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1).strip().lower()
    return from_header.strip().lower()


def safe_get_header(headers: list[dict], name: str) -> str:
    """
    Safely retrieves a header value from the Gmail message headers list.
    Returns an empty string if the header is not found.
    """
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""
