"""
Unit tests for the utils module.

Tests cover:
- JSON log formatting
- Retry decorator behaviour
- Email parsing helpers (base64url decoding, address extraction, header lookup)
"""

import base64
import json
import logging
import sys
import os

import pytest

# Ensure the lambda directory is on the import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lambda"))

from utils import (
    JSONFormatter,
    decode_base64url,
    extract_email_address,
    get_logger,
    retry,
    safe_get_header,
)


# ---------------------------------------------------------------------------
# JSONFormatter tests
# ---------------------------------------------------------------------------

class TestJSONFormatter:
    def test_basic_log_entry(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["message"] == "Hello world"
        assert data["logger"] == "test"
        assert "timestamp" in data

    def test_extra_data_merged(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="event",
            args=(),
            exc_info=None,
        )
        record.extra_data = {"message_id": "abc123"}
        output = formatter.format(record)
        data = json.loads(output)

        assert data["message_id"] == "abc123"


# ---------------------------------------------------------------------------
# Retry decorator tests
# ---------------------------------------------------------------------------

class TestRetry:
    def test_succeeds_on_first_attempt(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def always_works():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert always_works() == "ok"
        assert call_count == 1

    def test_retries_then_succeeds(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def fails_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient error")
            return "ok"

        assert fails_twice() == "ok"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        @retry(max_attempts=2, base_delay=0.01)
        def always_fails():
            raise RuntimeError("permanent failure")

        with pytest.raises(RuntimeError, match="permanent failure"):
            always_fails()

    def test_only_retries_specified_exceptions(self):
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, exceptions=(ValueError,))
        def wrong_exception():
            nonlocal call_count
            call_count += 1
            raise TypeError("should not retry")

        with pytest.raises(TypeError):
            wrong_exception()

        assert call_count == 1  # No retries for TypeError


# ---------------------------------------------------------------------------
# Email parsing helper tests
# ---------------------------------------------------------------------------

class TestDecodeBase64Url:
    def test_decode_simple_string(self):
        original = "Hello, World!"
        encoded = base64.urlsafe_b64encode(original.encode()).decode().rstrip("=")
        assert decode_base64url(encoded) == original

    def test_decode_with_unicode(self):
        original = "Subject: Überprüfung"
        encoded = base64.urlsafe_b64encode(original.encode()).decode().rstrip("=")
        assert decode_base64url(encoded) == original


class TestExtractEmailAddress:
    def test_angle_bracket_format(self):
        assert extract_email_address("John Doe <john@example.com>") == "john@example.com"

    def test_bare_email(self):
        assert extract_email_address("john@example.com") == "john@example.com"

    def test_case_insensitive(self):
        assert extract_email_address("John <JOHN@Example.COM>") == "john@example.com"

    def test_extra_whitespace(self):
        assert extract_email_address("  user@test.com  ") == "user@test.com"


class TestSafeGetHeader:
    def test_found(self):
        headers = [
            {"name": "Subject", "value": "Hello"},
            {"name": "From", "value": "alice@test.com"},
        ]
        assert safe_get_header(headers, "Subject") == "Hello"

    def test_not_found(self):
        headers = [{"name": "Subject", "value": "Hello"}]
        assert safe_get_header(headers, "To") == ""

    def test_case_insensitive(self):
        headers = [{"name": "FROM", "value": "alice@test.com"}]
        assert safe_get_header(headers, "from") == "alice@test.com"
