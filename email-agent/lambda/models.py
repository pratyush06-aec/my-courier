"""
Pydantic models for structured data throughout the email agent pipeline.

These models enforce data validation at boundaries (Gmail API response parsing,
Bedrock output parsing, DynamoDB serialization) and provide a single source
of truth for the shape of data flowing through the system.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class EmailMetadata(BaseModel):
    """
    Raw metadata extracted directly from a Gmail message.
    This is the input to the Bedrock summarization step.
    """
    message_id: str = Field(..., description="Gmail message ID (unique identifier)")
    thread_id: str = Field(default="", description="Gmail thread ID")
    sender: str = Field(..., description="From header value")
    sender_email: str = Field(default="", description="Parsed sender email address")
    to: str = Field(default="", description="To header value")
    subject: str = Field(default="", description="Email subject line")
    date: str = Field(default="", description="Date header value")
    snippet: str = Field(default="", description="Gmail snippet (short preview)")
    body_text: str = Field(default="", description="Plain-text body of the email")
    body_html: str = Field(default="", description="HTML body of the email (fallback)")
    labels: List[str] = Field(default_factory=list, description="Gmail label IDs")
    received_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
        description="ISO timestamp when the agent received this email",
    )


class BedrockSummary(BaseModel):
    """
    Structured output produced by Amazon Bedrock after analysing an email.
    The prompt instructs the model to return JSON matching this schema.
    """
    subject: str = Field(default="", description="Email subject")
    sender: str = Field(default="", description="Sender display name and address")
    priority: str = Field(default="Normal", description="Inferred priority: Low / Normal / High / Urgent")
    deadline: Optional[str] = Field(default=None, description="Extracted deadline, if any")
    action_items: List[str] = Field(default_factory=list, description="List of action items")
    one_line_summary: str = Field(default="", description="One-line summary of the email")


class ProcessedEmail(BaseModel):
    """
    The final record stored in DynamoDB combining raw metadata and
    the AI-generated summary.  Also used as the payload for notifications.
    """
    message_id: str
    thread_id: str = ""
    sender: str
    sender_email: str = ""
    subject: str
    date: str = ""
    snippet: str = ""
    priority: str = "Normal"
    deadline: Optional[str] = None
    action_items: List[str] = Field(default_factory=list)
    one_line_summary: str = ""
    processed_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    notified_slack: bool = False
    notified_whatsapp: bool = False


class PubSubNotification(BaseModel):
    """
    Represents the decoded payload from a Google Cloud Pub/Sub push message.
    Google Pub/Sub delivers the Gmail watch notification in this shape.
    """
    email_address: str = Field(..., description="The Gmail address that received mail")
    history_id: int = Field(..., description="The historyId to query from")
