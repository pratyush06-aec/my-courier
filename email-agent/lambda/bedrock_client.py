"""
Amazon Bedrock client — generates structured AI summaries of emails.

Design decisions:
- Uses Claude 3 Haiku (``anthropic.claude-3-haiku-20240307-v1:0``) by default.
  Haiku is the cheapest and fastest Claude model, ideal for short
  summarisation tasks where latency matters (Lambda timeout budget).
- The BEDROCK_REGION defaults to ``us-east-1`` because Bedrock model
  availability varies by region.  eu-north-1 may not have all models.
  This is configurable via the BEDROCK_REGION env var.
- The prompt instructs the model to return **strict JSON** matching our
  ``BedrockSummary`` Pydantic model so downstream consumers (Slack,
  WhatsApp, DynamoDB) get a validated, structured object.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import boto3

from models import BedrockSummary, EmailMetadata
from utils import get_logger, retry

logger = get_logger(__name__)

# Bedrock may not be available in eu-north-1; default to us-east-1
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)

_PROMPT_TEMPLATE = """\
You are an executive email assistant.  Analyse the following email and produce
a JSON object with EXACTLY these fields (no markdown fences, just raw JSON):

{{
  "subject": "<email subject>",
  "sender": "<sender name and address>",
  "priority": "<Low | Normal | High | Urgent>",
  "deadline": "<extracted deadline or null if none>",
  "action_items": ["<action item 1>", "..."],
  "one_line_summary": "<one crisp sentence summarising the email>"
}}

Rules for priority:
- Urgent: contains words like "ASAP", "immediately", "deadline today".
- High: mentions a specific near-term deadline or requires a response.
- Normal: informational with no deadline.
- Low: newsletters, FYIs, no action required.

Email:
Subject: {subject}
From: {sender}
Date: {date}
Body:
{body}
"""


@retry(max_attempts=2, base_delay=2.0)
def summarise_email(email: EmailMetadata) -> BedrockSummary:
    """
    Invokes Amazon Bedrock to produce a structured summary.

    Args:
        email: The raw email metadata extracted from Gmail.

    Returns:
        A validated ``BedrockSummary`` instance.
    """
    body_content = email.body_text or email.snippet or "(no body)"
    # Truncate very long emails to stay within model context and reduce cost.
    if len(body_content) > 4000:
        body_content = body_content[:4000] + "\n[...truncated...]"

    prompt = _PROMPT_TEMPLATE.format(
        subject=email.subject,
        sender=email.sender,
        date=email.date,
        body=body_content,
    )

    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

    request_body: Dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "temperature": 0.0,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }

    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(request_body),
    )

    response_body = json.loads(response["body"].read())
    raw_text: str = response_body["content"][0]["text"]

    logger.info("Bedrock raw response length: %d chars", len(raw_text))

    # Parse the JSON from the model response
    try:
        summary_dict = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning(
            "Bedrock returned non-JSON; falling back to basic summary"
        )
        summary_dict = {
            "subject": email.subject,
            "sender": email.sender,
            "priority": "Normal",
            "deadline": None,
            "action_items": [],
            "one_line_summary": email.snippet or "Unable to parse summary.",
        }

    summary = BedrockSummary(**summary_dict)
    logger.info(
        "Bedrock summary: priority=%s, action_items=%d",
        summary.priority,
        len(summary.action_items),
    )
    return summary
