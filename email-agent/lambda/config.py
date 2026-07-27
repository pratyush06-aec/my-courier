import os
from typing import List

# dotenv is only needed for local development.
# In Lambda, environment variables are set by the runtime directly.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class Config:
    """
    Configuration management class. 
    In production, some of these might be fetched from AWS Secrets Manager.
    """
    # AWS Region
    AWS_REGION: str = os.getenv("AWS_REGION", "eu-north-1")

    # Agent settings
    ALLOWED_SENDERS: List[str] = os.getenv(
        "ALLOWED_SENDERS", 
        "StudentBuilders@amazon.com,judkatha@amazon.com"
    ).split(",")

    # Gmail settings
    GMAIL_CREDENTIALS_JSON_PATH: str = os.getenv("GMAIL_CREDENTIALS_JSON_PATH", "./lambda/credentials.json")
    GMAIL_TOKEN_JSON_PATH: str = os.getenv("GMAIL_TOKEN_JSON_PATH", "token.json")

    # Twilio settings
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")
    TWILIO_TO_NUMBER: str = os.getenv("TWILIO_TO_NUMBER", "")

    # Slack settings
    SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")

    # DynamoDB settings
    DYNAMODB_TABLE_NAME: str = os.getenv("DYNAMODB_TABLE_NAME", "EmailAgentProcessedMessages")

    @classmethod
    def validate(cls):
        """
        Validates that required configuration variables are present.
        """
        # We will expand validation as we progress through phases.
        pass

# Instantiate config for easier import
config = Config()
